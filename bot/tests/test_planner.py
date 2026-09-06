import json
import unittest
from unittest.mock import MagicMock, patch

from src.planner import BotToolError, BotWebSocketClient, OllamaClient, Planner, TOOLS


class BotWebSocketClientTests(unittest.TestCase):
    def test_request_correlates_response_and_returns_result(self):
        connection = MagicMock()
        connection.recv.return_value = json.dumps(
            {
                "version": 1,
                "id": "planner-1",
                "ok": True,
                "result": {"health": 20},
            }
        )
        client = BotWebSocketClient()
        client._connection = connection

        result = client.request("get_status")

        self.assertEqual(result, {"health": 20})
        sent = json.loads(connection.send.call_args.args[0])
        self.assertEqual(sent["id"], "planner-1")
        self.assertEqual(sent["method"], "get_status")
        self.assertEqual(sent["params"], {})

    def test_request_raises_structured_bot_error(self):
        connection = MagicMock()
        connection.recv.return_value = json.dumps(
            {
                "version": 1,
                "id": "planner-1",
                "ok": False,
                "error": {"code": "BOT_ERROR", "message": "no food available"},
            }
        )
        client = BotWebSocketClient()
        client._connection = connection

        with self.assertRaises(BotToolError) as raised:
            client.request("eat")

        self.assertEqual(raised.exception.code, "BOT_ERROR")
        self.assertEqual(str(raised.exception), "no food available")

    def test_request_rejects_mismatched_response_id(self):
        connection = MagicMock()
        connection.recv.return_value = json.dumps(
            {"version": 1, "id": "wrong", "ok": True, "result": {}}
        )
        client = BotWebSocketClient()
        client._connection = connection

        with self.assertRaisesRegex(RuntimeError, "did not match"):
            client.request("observe")


class OllamaClientTests(unittest.TestCase):
    def test_chat_posts_non_streaming_tool_request(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"message":{"role":"assistant","content":"done"}}'
        client = OllamaClient("qwen3", base_url="http://ollama.test")

        with patch(
            "src.planner.urllib.request.urlopen", return_value=response
        ) as urlopen:
            message = client.chat([{"role": "user", "content": "goal"}], TOOLS)

        self.assertEqual(message["content"], "done")
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "http://ollama.test/api/chat")
        self.assertEqual(payload["model"], "qwen3")
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["think"])
        self.assertEqual(payload["options"]["num_ctx"], 8192)
        self.assertEqual(payload["options"]["num_predict"], 512)
        self.assertEqual(payload["tools"], TOOLS)

    def test_chat_reports_response_timeout(self):
        client = OllamaClient("qwen3", base_url="http://ollama.test", timeout=5)

        with patch(
            "src.planner.urllib.request.urlopen", side_effect=TimeoutError
        ):
            with self.assertRaisesRegex(RuntimeError, "within 5 seconds"):
                client.chat([{"role": "user", "content": "goal"}], TOOLS)


class FakeBot:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def request(self, method, params=None):
        self.calls.append((method, params))
        response = self.responses.get(method, {})
        if isinstance(response, Exception):
            raise response
        return response


class FakeOllama:
    def __init__(self, messages):
        self.responses = iter(messages)
        self.calls = []

    def chat(self, messages, tools):
        self.calls.append((list(messages), tools))
        return next(self.responses)


class PlannerTests(unittest.TestCase):
    def test_planner_executes_tool_and_returns_final_response(self):
        bot = FakeBot(
            {
                "observe": {"inventory": []},
                "get_inventory_counts": {"oak_log": 3},
            }
        )
        ollama = FakeOllama(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "get_inventory_counts",
                                "arguments": {},
                            }
                        }
                    ],
                },
                {"role": "assistant", "content": "DONE: Goal verified.", "tool_calls": []},
            ]
        )

        result = Planner(bot, ollama).run("collect three logs")

        self.assertEqual(result, "Goal verified.")
        self.assertEqual(
            bot.calls,
            [("observe", None), ("get_inventory_counts", {})],
        )
        second_messages = ollama.calls[1][0]
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertEqual(second_messages[-1]["tool_name"], "get_inventory_counts")
        self.assertEqual(
            json.loads(second_messages[-1]["content"]),
            {"ok": True, "result": {"oak_log": 3}},
        )

    def test_planner_returns_bot_failure_to_model_for_replanning(self):
        bot = FakeBot(
            {
                "observe": {},
                "eat": BotToolError("BOT_ERROR", "no food available"),
            }
        )
        ollama = FakeOllama(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "eat", "arguments": {}}}
                    ],
                },
                {
                    "role": "assistant",
                    "content": "BLOCKED: Cannot eat because no food is available.",
                },
            ]
        )

        result = Planner(bot, ollama).run("eat food")

        self.assertIn("no food", result)
        tool_result = json.loads(ollama.calls[1][0][-1]["content"])
        self.assertEqual(tool_result["error"]["code"], "BOT_ERROR")

    def test_planner_reprompts_after_empty_response(self):
        bot = FakeBot({"observe": {}, "get_status": {"action": "idle"}})
        ollama = FakeOllama(
            [
                {"role": "assistant", "content": ""},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "get_status", "arguments": {}}}
                    ],
                },
                {"role": "assistant", "content": "BLOCKED: Goal is blocked."},
            ]
        )

        result = Planner(bot, ollama).run("collect a log")

        self.assertEqual(result, "Goal is blocked.")
        retry_message = ollama.calls[1][0][-1]
        self.assertEqual(retry_message["role"], "user")
        self.assertIn("previous response was empty", retry_message["content"])

    def test_planner_stops_after_repeated_empty_responses(self):
        bot = FakeBot({"observe": {}})
        empty = {"role": "assistant", "content": ""}

        result = Planner(bot, FakeOllama([empty, empty, empty])).run("collect a log")

        self.assertIn("could not produce an executable tool call", result)

    def test_planner_accepts_unmarked_conclusion(self):
        bot = FakeBot({"observe": {}, "get_status": {"action": "idle"}})
        ollama = FakeOllama(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "get_status", "arguments": {}}}
                    ],
                },
                {"role": "assistant", "content": "The requested log was collected."},
            ]
        )

        result = Planner(bot, ollama).run("collect a log")

        self.assertEqual(result, "The requested log was collected.")

    def test_planner_rejects_generic_instructions_before_any_tool_call(self):
        bot = FakeBot({"observe": {}, "get_inventory_counts": {}})
        ollama = FakeOllama(
            [
                {
                    "role": "assistant",
                    "content": "To craft a pickaxe, follow these steps and gather materials.",
                },
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "get_inventory_counts",
                                "arguments": {},
                            }
                        }
                    ],
                },
                {"role": "assistant", "content": "BLOCKED: Missing wood."},
            ]
        )

        result = Planner(bot, ollama).run("craft a wooden pickaxe")

        self.assertEqual(result, "Missing wood.")

    def test_planner_reprompts_when_prose_proposes_an_action(self):
        bot = FakeBot({"observe": {}, "get_status": {"action": "idle"}})
        ollama = FakeOllama(
            [
                {
                    "role": "assistant",
                    "content": "Action: use move_near to find a safer location.",
                },
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "get_status", "arguments": {}}}
                    ],
                },
                {"role": "assistant", "content": "BLOCKED: No route is available."},
            ]
        )

        result = Planner(bot, ollama).run("collect a log")

        self.assertEqual(result, "No route is available.")
        retry_message = ollama.calls[1][0][-1]
        self.assertIn("Invoke exactly one available tool", retry_message["content"])

    def test_unknown_tool_is_returned_as_error_without_bot_call(self):
        bot = FakeBot({"observe": {}})
        planner = Planner(bot, FakeOllama([]))

        result = planner._execute_tool("teleport", {})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "UNKNOWN_TOOL")
        self.assertEqual(bot.calls, [])

    def test_search_no_match_is_explicit(self):
        bot = FakeBot({"observe": {}, "find_nearest_block": None})
        planner = Planner(bot, FakeOllama([]))

        result = planner._execute_tool(
            "find_nearest_block", {"block_type": "oak_log", "radius": 10}
        )

        self.assertEqual(result, {"ok": True, "result": None, "found": False})

    def test_planner_rejects_exploration_only_blocked_conclusion(self):
        bot = FakeBot(
            {
                "observe": {},
                "find_nearest_blocks": [],
                "wander": None,
                "mine_nearest_tree": BotToolError("BOT_ERROR", "no loaded tree"),
            }
        )
        ollama = FakeOllama(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "find_nearest_blocks",
                                "arguments": {
                                    "block_types": ["oak_log"],
                                    "radius": 64,
                                },
                            }
                        }
                    ],
                },
                {"role": "assistant", "content": "BLOCKED: No logs nearby."},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "wander", "arguments": {"radius": 32}}}
                    ],
                },
                {"role": "assistant", "content": "BLOCKED: Exploration failed."},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "mine_nearest_tree",
                                "arguments": {"radius": 128},
                            }
                        }
                    ],
                },
                {"role": "assistant", "content": "BLOCKED: No loaded tree exists."},
            ]
        )

        result = Planner(bot, ollama).run("collect a log")

        self.assertEqual(result, "No loaded tree exists.")
        self.assertEqual(bot.calls[-1], ("mine_nearest_tree", {"radius": 128}))
        retry_message = ollama.calls[2][0][-1]
        self.assertIn("concrete collection workflow", retry_message["content"])

    def test_planner_rejects_bare_blocked_marker(self):
        bot = FakeBot({"observe": {}, "get_status": {"action": "idle"}})
        ollama = FakeOllama(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "get_status", "arguments": {}}}
                    ],
                },
                {"role": "assistant", "content": "BLOCKED"},
                {"role": "assistant", "content": "BLOCKED: No route exists."},
            ]
        )

        result = Planner(bot, ollama).run("travel")

        self.assertEqual(result, "No route exists.")
        retry_message = ollama.calls[2][0][-1]
        self.assertIn("followed by a concrete blocker", retry_message["content"])

    def test_tree_goal_requires_tree_workflow_after_failed_wander(self):
        bot = FakeBot(
            {
                "observe": {},
                "get_recipes": [],
                "find_nearest_block": None,
                "wander": BotToolError("BOT_ERROR", "PathStopped"),
                "mine_nearest_tree": BotToolError("BOT_ERROR", "no loaded tree"),
            }
        )
        ollama = FakeOllama(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "get_recipes",
                                "arguments": {"item_name": "wooden_pickaxe"},
                            }
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "find_nearest_block",
                                "arguments": {"block_type": "oak_log", "radius": 16},
                            }
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "wander", "arguments": {"radius": 16}}}
                    ],
                },
                {"role": "assistant", "content": "BLOCKED: PathStopped."},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "mine_nearest_tree",
                                "arguments": {"radius": 128},
                            }
                        }
                    ],
                },
                {"role": "assistant", "content": "BLOCKED: No loaded tree exists."},
            ]
        )

        result = Planner(bot, ollama).run("craft a pickaxe then mine a tree")

        self.assertEqual(result, "No loaded tree exists.")
        self.assertEqual(bot.calls[-1], ("mine_nearest_tree", {"radius": 128}))
        retry_message = ollama.calls[4][0][-1]
        self.assertIn("required tree workflow", retry_message["content"])

    def test_plural_search_reports_result_count(self):
        blocks = [{"type": "oak_log", "position": [1, 2, 3]}]
        bot = FakeBot({"observe": {}, "find_nearest_blocks": blocks})
        planner = Planner(bot, FakeOllama([]))

        result = planner._execute_tool(
            "find_nearest_blocks",
            {"block_types": ["oak_log"], "radius": 16},
        )

        self.assertEqual(result["count"], 1)

    def test_incomplete_collection_reports_reason(self):
        bot = FakeBot(
            {
                "observe": {},
                "collect_resource": {"requested": 1, "mined": 0, "gained": {}},
            }
        )
        planner = Planner(bot, FakeOllama([]))

        result = planner._execute_tool(
            "collect_resource", {"block_type": "oak_log", "quantity": 1}
        )

        self.assertFalse(result["completed"])
        self.assertEqual(result["reason"], "NO_MATCH")

    def test_tree_mining_reports_completion(self):
        bot = FakeBot(
            {
                "observe": {},
                "mine_nearest_tree": {
                    "completed": True,
                    "logs_found": 5,
                    "logs_mined": 5,
                },
            }
        )
        planner = Planner(bot, FakeOllama([]))

        result = planner._execute_tool("mine_nearest_tree", {"radius": 64})

        self.assertTrue(result["completed"])

    def test_planner_enforces_step_limit(self):
        bot = FakeBot({"observe": {}, "get_status": {"health": 20}})
        tool_message = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "get_status", "arguments": {}}}
            ],
        }
        ollama = FakeOllama([tool_message, tool_message])

        result = Planner(bot, ollama, max_steps=2).run("never finish")

        self.assertIn("BLOCKED: Planner reached its 2-step limit", result)
        self.assertIn("Goal not verified", result)
        self.assertIn("Last tool: get_status", result)
        self.assertEqual(len(ollama.calls), 2)
        self.assertEqual(len(bot.calls), 3)

    def test_step_limit_preserves_last_tool_error(self):
        bot = FakeBot({"wander": BotToolError("BOT_ERROR", "PathStopped")})
        ollama = FakeOllama([
            {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "wander", "arguments": {"radius": 16}}}
                ],
            }
        ])

        result = Planner(bot, ollama, max_steps=1).run("explore")

        self.assertIn("Goal not verified", result)
        self.assertIn("PathStopped", result)

    def test_rejected_blockers_obey_consecutive_retry_limit(self):
        bot = FakeBot()
        blocked = {"role": "assistant", "content": "BLOCKED: No oak nearby."}
        ollama = FakeOllama([blocked, blocked, blocked])

        result = Planner(bot, ollama).run("mine a tree")

        self.assertIn("3 consecutive responses", result)
        self.assertIn("Goal not verified", result)
        self.assertEqual(len(ollama.calls), 3)
        self.assertEqual(bot.calls, [("observe", None)])

    def test_repeated_identical_failures_stop_before_step_limit(self):
        for batched in (False, True):
            with self.subTest(batched=batched):
                bot = FakeBot({"wander": BotToolError("BOT_ERROR", "PathStopped")})
                call = {"function": {"name": "wander", "arguments": {"radius": 16}}}
                message = {
                    "role": "assistant",
                    "tool_calls": [call] * (30 if batched else 1),
                }
                ollama = FakeOllama([message] * 30)

                result = Planner(bot, ollama).run("explore")

                self.assertIn("BLOCKED:", result)
                self.assertIn("3 consecutive identical tool failures", result)
                self.assertIn("Goal not verified", result)
                self.assertIn("BOT_ERROR", result)
                self.assertIn("PathStopped", result)
                self.assertIn('"radius":16', result)
                self.assertEqual(len(ollama.calls), 1 if batched else 3)
                self.assertEqual(
                    bot.calls,
                    [("observe", None)] + [("wander", {"radius": 16})] * 3,
                )

    def test_failure_streak_resets_on_success_or_different_failure(self):
        for name, arguments, response in (
            ("wander", {"radius": 16}, {}),
            ("wander", {"radius": 8}, BotToolError("BOT_ERROR", "PathStopped")),
            ("move_near", {"x": 1, "y": 64, "z": 2}, BotToolError("BOT_ERROR", "PathStopped")),
            ("wander", {"radius": 16}, BotToolError("BOT_ERROR", "NoPath")),
            ("wander", {"radius": 16}, BotToolError("NAVIGATION_STUCK", "PathStopped")),
        ):
            with self.subTest(name=name, arguments=arguments, response=response):
                failure = BotToolError("BOT_ERROR", "PathStopped")
                bot = MagicMock()
                bot.request.side_effect = [{}, failure, failure, response, failure, failure]
                call = {"function": {"name": "wander", "arguments": {"radius": 16}}}
                calls = [
                    call, call,
                    {"function": {"name": name, "arguments": arguments}},
                    call, call,
                ]
                ollama = FakeOllama([
                    {"role": "assistant", "tool_calls": [tool_call]}
                    for tool_call in calls
                ])

                result = Planner(bot, ollama, max_steps=5).run("explore")

                self.assertIn("5-step limit", result)
                self.assertEqual(bot.request.call_count, 6)

    def test_failure_streak_ignores_argument_key_order(self):
        bot = FakeBot({"move_near": BotToolError("BOT_ERROR", "PathStopped")})
        ollama = FakeOllama([
            {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "move_near", "arguments": arguments}}
                ],
            }
            for arguments in ({"x": 1, "y": 64, "z": 2}, {"z": 2, "y": 64, "x": 1}) * 2
        ])

        result = Planner(bot, ollama).run("move")

        self.assertIn("3 consecutive identical tool failures", result)
        self.assertEqual(len(bot.calls), 4)

    def test_tool_names_are_unique(self):
        names = [tool["function"]["name"] for tool in TOOLS]
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
