import json
import unittest
from unittest.mock import MagicMock

from javascript.errors import JavaScriptError

from src.bot import NavigationStuckError, NavigationTimeoutError, UnknownBlockTypeError
from src.protocol import handle_request


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.bot = MagicMock()

    def request(self, method, params=None, request_id="req-1", version=1):
        request = {"version": version, "id": request_id, "method": method}
        if params is not None:
            request["params"] = params
        return json.loads(handle_request(self.bot, json.dumps(request)))

    def test_ping_echoes_request_id(self):
        response = self.request("ping")

        self.assertEqual(
            response,
            {"version": 1, "id": "req-1", "ok": True, "result": {"pong": True}},
        )

    def test_disconnect_is_acknowledged(self):
        response = self.request("disconnect")

        self.assertEqual(response["result"], {"disconnected": True})

    def test_status_dispatches_to_bot(self):
        self.bot.get_status.return_value = {"action": "idle"}

        response = self.request("get_status")

        self.assertEqual(response["result"], {"action": "idle"})
        self.bot.get_status.assert_called_once_with()

    def test_invalid_json_has_null_request_id(self):
        response = json.loads(handle_request(self.bot, "{"))

        self.assertEqual(response["id"], None)
        self.assertEqual(response["error"]["code"], "INVALID_REQUEST")

    def test_unsupported_version_preserves_request_id(self):
        response = self.request("ping", version=2)

        self.assertEqual(response["id"], "req-1")
        self.assertEqual(response["error"]["code"], "UNSUPPORTED_VERSION")

    def test_unknown_method_returns_structured_error(self):
        response = self.request("dance")

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "METHOD_NOT_FOUND")

    def test_collect_resource_validates_and_dispatches_params(self):
        self.bot.collect_resource.return_value = {"mined": 2}

        response = self.request(
            "collect_resource", {"block_type": "oak_log", "quantity": 2}
        )

        self.assertEqual(response["result"], {"mined": 2})
        self.bot.collect_resource.assert_called_once_with("oak_log", 2, 32, 5.0)

    def test_mine_nearest_tree_validates_and_dispatches_params(self):
        self.bot.mine_nearest_tree.return_value = {
            "completed": True,
            "logs_mined": 5,
        }

        response = self.request(
            "mine_nearest_tree",
            {"radius": 80, "max_logs": 32, "timeout": 8},
        )

        self.assertTrue(response["result"]["completed"])
        self.bot.mine_nearest_tree.assert_called_once_with(80, 32, 8)

    def test_inventory_and_block_queries_dispatch(self):
        cases = (
            ("get_inventory", None, "get_inventory", (), {}, []),
            ("get_inventory_counts", None, "get_inventory_counts", (), {}, {}),
            (
                "find_inventory_items",
                {"item_name": "torch"},
                "find_inventory_items",
                ("torch",),
                {},
                [{"name": "torch"}],
            ),
            (
                "scan_nearby",
                {"radius": 12},
                "scan_nearby",
                (12,),
                {},
                {"entities": [], "block_counts": {}},
            ),
            (
                "get_dropped_items",
                None,
                "get_dropped_items",
                (8,),
                {},
                [],
            ),
            (
                "find_blocks",
                {"block_type": "stone", "radius": 16, "count": 3},
                "find_blocks",
                ("stone", 16, 3),
                {},
                [[1, 2, 3]],
            ),
            (
                "find_nearest_block",
                {"block_type": "stone", "radius": 16},
                "find_nearest_block",
                ("stone", 16),
                {},
                [1, 2, 3],
            ),
            (
                "find_nearest_blocks",
                {"block_types": ["stone", "coal_ore"], "radius": 16},
                "find_nearest_blocks",
                (["stone", "coal_ore"], 16),
                {},
                [],
            ),
        )

        for method, params, attribute, args, kwargs, result in cases:
            with self.subTest(method=method):
                function = getattr(self.bot, attribute)
                function.return_value = result
                response = self.request(method, params)

                self.assertEqual(response["result"], result)
                function.assert_called_once_with(*args, **kwargs)
                function.reset_mock()

    def test_look_and_navigation_actions_dispatch(self):
        cases = (
            ("look_at", {"x": 1, "y": 2.5, "z": -3}, (1, 2.5, -3), {}),
            ("look_at_block", {"x": 1, "y": 2, "z": 3}, (1, 2, 3), {}),
            ("look_at_entity", {"entity_id": 7}, (7,), {}),
            ("move_to", {"x": 1, "y": 2, "z": 3}, (1, 2, 3), {}),
            ("move_to_block", {"x": 1, "y": 2, "z": 3}, (1, 2, 3), {}),
            (
                "move_near",
                {"x": 1, "y": 2, "z": 3, "distance": 2.5},
                (1, 2, 3),
                {"distance": 2.5},
            ),
            ("wander", {"radius": 32}, (32,), {}),
            (
                "move_to_entity",
                {"entity_id": 7, "distance": 2.5},
                (7,),
                {"distance": 2.5},
            ),
            (
                "follow_entity",
                {"entity_id": 7, "distance": 3.5},
                (7,),
                {"distance": 3.5},
            ),
        )

        for method, params, args, kwargs in cases:
            with self.subTest(method=method):
                response = self.request(method, params)

                self.assertEqual(response["result"], {"completed": True})
                getattr(self.bot, method).assert_called_once_with(*args, **kwargs)
                getattr(self.bot, method).reset_mock()

    def test_plural_block_search_accepts_result_limit(self):
        self.bot.find_nearest_blocks.return_value = []

        response = self.request(
            "find_nearest_blocks",
            {
                "block_types": ["oak_log", "birch_log"],
                "radius": 50,
                "max_results": 8,
            },
        )

        self.assertTrue(response["ok"])
        self.bot.find_nearest_blocks.assert_called_once_with(
            ["oak_log", "birch_log"], 50, 8
        )

    def test_movement_controls_dispatch(self):
        for method in (
            "walk_forward",
            "walk_backward",
            "strafe_left",
            "strafe_right",
        ):
            with self.subTest(method=method):
                response = self.request(method, {"duration": 0.5})

                self.assertEqual(response["result"], {"completed": True})
                getattr(self.bot, method).assert_called_once_with(0.5)

        for method in ("jump", "stop_movement", "stop_navigation"):
            with self.subTest(method=method):
                response = self.request(method)

                self.assertEqual(response["result"], {"completed": True})
                getattr(self.bot, method).assert_called_once_with()

        self.bot.recalculate_path.return_value = [[0, 0, 0], [1, 0, 0]]
        response = self.request("recalculate_path")
        self.assertEqual(response["result"], [[0, 0, 0], [1, 0, 0]])

    def test_mining_collection_tool_and_chat_actions_dispatch(self):
        self.bot.mine_block.return_value = True
        response = self.request("mine_block", {"x": 1, "y": 2, "z": 3})
        self.assertEqual(response["result"], {"mined": True})
        self.bot.mine_block.assert_called_once_with(1, 2, 3)

        placement = {
            "block_type": "cobblestone",
            "position": {"x": 1, "y": 2, "z": 3},
        }
        self.bot.place_block.return_value = placement
        response = self.request(
            "place_block",
            {"block_type": "cobblestone", "x": 1, "y": 2, "z": 3},
        )
        self.assertEqual(response["result"], placement)
        self.bot.place_block.assert_called_once_with(
            "cobblestone", 1, 2, 3, timeout=3.0
        )

        tool = {"name": "iron_pickaxe"}
        self.bot.equip_best_tool_for_block.return_value = tool
        response = self.request(
            "equip_best_tool_for_block", {"block_type": "stone"}
        )
        self.assertEqual(response["result"], tool)

        self.bot.collect_dropped_items.return_value = [4, 5]
        response = self.request(
            "collect_dropped_items", {"timeout": 2.5, "radius": 6}
        )
        self.assertEqual(response["result"], [4, 5])
        self.bot.collect_dropped_items.assert_called_once_with(timeout=2.5, radius=6)

        self.bot.send_chat.return_value = True
        response = self.request("send_chat", {"message": "hello"})
        self.assertEqual(response["result"], {"sent": True})
        self.bot.send_chat.assert_called_once_with("hello")

    def test_get_recipes_and_craft_item_dispatch(self):
        recipes = [{"result_name": "wooden_pickaxe", "crafts_possible": 0}]
        self.bot.get_recipes.return_value = recipes
        response = self.request("get_recipes", {"item_name": "wooden_pickaxe"})
        self.assertEqual(response["result"], recipes)
        self.bot.get_recipes.assert_called_once_with("wooden_pickaxe")

        self.bot.craft_item.return_value = {"crafted": 1}
        response = self.request(
            "craft_item", {"item_name": "wooden_pickaxe", "quantity": 2}
        )
        self.assertEqual(response["result"], {"crafted": 1})
        self.bot.craft_item.assert_called_once_with("wooden_pickaxe", 2)

        response = self.request("craft_item", {"item_name": "oak_planks"})
        self.bot.craft_item.assert_called_with("oak_planks", 1)

    def test_eat_dispatches_optional_item_name(self):
        self.bot.eat.return_value = {"eaten": "apple", "food_level": 18.0}

        response = self.request("eat", {"item_name": "apple"})
        self.assertEqual(response["result"]["eaten"], "apple")
        self.bot.eat.assert_called_once_with("apple")

        self.bot.eat.reset_mock()
        response = self.request("eat")
        self.assertEqual(response["result"]["food_level"], 18.0)
        self.bot.eat.assert_called_once_with(None)

    def test_invalid_params_are_rejected(self):
        response = self.request(
            "collect_resource", {"block_type": "oak_log", "quantity": 0}
        )

        self.assertEqual(response["error"]["code"], "INVALID_PARAMS")
        self.bot.collect_resource.assert_not_called()

    def test_action_params_reject_invalid_values(self):
        cases = (
            ("jump", {"extra": True}),
            ("move_to", {"x": True, "y": 2, "z": 3}),
            ("move_to", {"x": float("inf"), "y": 2, "z": 3}),
            ("find_nearest_blocks", {"block_types": [], "radius": 8}),
            (
                "place_block",
                {"block_type": "stone", "x": 1, "y": 2, "z": 3, "timeout": 0},
            ),
            ("send_chat", {"message": "x" * 257}),
            ("craft_item", {"item_name": "oak_planks", "quantity": 0}),
            ("craft_item", {"item_name": "oak_planks", "extra": 1}),
            ("get_recipes", {}),
            ("eat", {"item_name": ""}),
            ("eat", {"extra": True}),
        )

        for method, params in cases:
            with self.subTest(method=method):
                response = self.request(method, params)
                self.assertEqual(response["error"]["code"], "INVALID_PARAMS")

    def test_expected_bot_failure_returns_bot_error(self):
        self.bot.observe.side_effect = RuntimeError("bot is not connected")

        response = self.request("observe")

        self.assertEqual(response["error"]["code"], "BOT_ERROR")
        self.assertEqual(response["error"]["message"], "bot is not connected")

    def test_javascript_failure_returns_visible_bot_error(self):
        self.bot.move_near.side_effect = JavaScriptError(
            "goto", "Error: No path to the goal\n    at pathfinder.js:1:1"
        )

        response = self.request(
            "move_near", {"x": 6, "y": 16, "z": -104, "distance": 10}
        )

        self.assertEqual(response["error"]["code"], "BOT_ERROR")
        self.assertEqual(response["error"]["message"], "Error: No path to the goal")

    def test_unknown_block_type_has_specific_error_code(self):
        self.bot.find_nearest_block.side_effect = UnknownBlockTypeError(
            "unknown block type: log; did you mean: oak_log"
        )

        response = self.request(
            "find_nearest_block", {"block_type": "log", "radius": 16}
        )

        self.assertEqual(response["error"]["code"], "UNKNOWN_BLOCK_TYPE")
        self.assertIn("oak_log", response["error"]["message"])

    def test_navigation_failures_return_bot_errors(self):
        failures = (
            NavigationStuckError("navigation is stuck"),
            NavigationTimeoutError("navigation timed out"),
        )

        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.bot.collect_resource.side_effect = failure
                response = self.request(
                    "collect_resource", {"block_type": "stone", "quantity": 1}
                )

                self.assertEqual(response["error"]["code"], "BOT_ERROR")
                self.assertEqual(response["error"]["message"], str(failure))

    def test_unexpected_failure_hides_details(self):
        self.bot.observe.side_effect = TypeError("secret detail")

        response = self.request("observe")

        self.assertEqual(response["error"]["code"], "INTERNAL_ERROR")
        self.assertEqual(response["error"]["message"], "unexpected server error")


if __name__ == "__main__":
    unittest.main()
