import json
import logging
import urllib.error
import urllib.request
from itertools import count

from websockets.sync.client import connect


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


SYSTEM_PROMPT = """You control a Minecraft survival bot through tools.
Work iteratively: inspect state, take an action, inspect the result, and continue.
Treat tool errors as observations and revise the plan instead of repeating blindly.
Increasing a search radius does not make an already-found block more reachable. If
movement to a block returns NoPath, do not search at a larger radius and retry the
same coordinates; use the matching collection workflow or explore elsewhere.
Use canonical Minecraft registry names in snake_case (for example oak_log or
oak_sapling), never category names or invented aliases such as tree, log, or
sapling. Once a block name is accepted, keep using it. A successful search with
found=false means no matching loaded block is within that radius, not that the
name is invalid. Use wander to load different terrain, then search again. For
wood, use mine_nearest_tree rather than assuming only oak is acceptable.
For tree goals, search log variants only; leaves are not tree trunks and should
not be included as collection targets. For requests to mine or fell a whole
tree, you must call mine_nearest_tree instead of searching only for oak or
guessing a collect_resource quantity.
If mine_nearest_tree fails, observe or report its concrete blocker; do not call
move_near on a high log coordinate because canopy blocks are not standable.
Prefer collect_resource when the canonical target is known. If it reports
completed=false, expand the radius or explain the blocker. Make one tool call at
a time so each result can guide the next action.
craft_item quantity is the number of recipe executions; inspect get_recipes for
result_count and ingredients. Eat when hunger could prevent longer work.
Only finish when the goal is verified from inventory or observation. If the goal
cannot be completed with the available tools, explain the concrete blocker.
Final responses must start with DONE: for a verified goal or BLOCKED: when no
available tool can make further progress. Never describe a future action in a
final response: invoke that tool instead. Do not ask the user to choose movement
coordinates; observe and select coordinates yourself. Do not answer with generic
Minecraft instructions; you must operate the connected bot through tools."""


def _tool(name, description, properties=None, required=()):
    parameters = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        parameters["required"] = list(required)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


STRING = {"type": "string"}
POSITIVE_INTEGER = {"type": "integer", "minimum": 1}
POSITIVE_NUMBER = {"type": "number", "exclusiveMinimum": 0}
COORDINATE = {"type": "number"}

TOOLS = [
    _tool("observe", "Observe bot status, inventory, entities, and nearby blocks."),
    _tool("get_status", "Get connection, position, health, hunger, and current action."),
    _tool("get_inventory_counts", "Get item counts grouped by item name."),
    _tool(
        "find_nearest_blocks",
        "Find loaded blocks of several canonical registry types, ordered by distance. "
        "Use exact snake_case names such as oak_log; an empty result means no match "
        "was loaded within the radius.",
        {
            "block_types": {"type": "array", "items": STRING, "minItems": 1},
            "radius": POSITIVE_INTEGER,
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 32,
                "description": "Maximum results to return; defaults to 16.",
            },
        },
        ("block_types", "radius"),
    ),
    _tool(
        "find_nearest_block",
        "Find the nearest loaded block of one canonical registry type. Use an exact "
        "snake_case name such as oak_log; found=false means no loaded match within "
        "the radius. Results are ordered by geometric distance, not reachability; "
        "increasing the radius will not change an already-found nearest result.",
        {"block_type": STRING, "radius": POSITIVE_INTEGER},
        ("block_type", "radius"),
    ),
    _tool(
        "get_recipes",
        "Get recipes, required ingredients, missing ingredients, and output count.",
        {"item_name": STRING},
        ("item_name",),
    ),
    _tool(
        "collect_resource",
        "Find, navigate to, mine, collect, and verify blocks of one canonical "
        "registry type such as oak_log. completed=false means fewer than requested "
        "were collected within the radius.",
        {
            "block_type": STRING,
            "quantity": POSITIVE_INTEGER,
            "radius": POSITIVE_INTEGER,
            "timeout": POSITIVE_NUMBER,
        },
        ("block_type", "quantity"),
    ),
    _tool(
        "mine_nearest_tree",
        "Mine every connected log in the nearest loaded tree. Uses harvested logs "
        "as temporary scaffolding for high blocks, then removes the scaffolding. "
        "Do not replace this workflow with move_near calls to canopy coordinates.",
        {
            "radius": POSITIVE_INTEGER,
            "max_logs": POSITIVE_INTEGER,
            "timeout": POSITIVE_NUMBER,
        },
    ),
    _tool(
        "craft_item",
        "Craft an item, automatically locating or placing a crafting table.",
        {"item_name": STRING, "quantity": POSITIVE_INTEGER},
        ("item_name",),
    ),
    _tool(
        "eat",
        "Eat one named food, or the best available food when item_name is omitted.",
        {"item_name": STRING},
    ),
    _tool(
        "move_near",
        "Navigate near world coordinates. Solid block coordinates use block-aware "
        "navigation. After NoPath, do not retry the same coordinates or increase a "
        "block-search radius; use a collection workflow or a different destination.",
        {
            "x": COORDINATE,
            "y": COORDINATE,
            "z": COORDINATE,
            "distance": POSITIVE_NUMBER,
        },
        ("x", "y", "z"),
    ),
    _tool(
        "wander",
        "Explore in a random direction to load different terrain. Use this after "
        "block searches find no loaded matches; then search again.",
        {"radius": POSITIVE_NUMBER},
        ("radius",),
    ),
    _tool(
        "mine_block",
        "Mine one reachable block at exact world coordinates. Only call this after "
        "successful navigation has brought the bot within 4.5 blocks.",
        {"x": COORDINATE, "y": COORDINATE, "z": COORDINATE},
        ("x", "y", "z"),
    ),
    _tool(
        "place_block",
        "Place one inventory block at exact world coordinates.",
        {
            "block_type": STRING,
            "x": COORDINATE,
            "y": COORDINATE,
            "z": COORDINATE,
            "timeout": POSITIVE_NUMBER,
        },
        ("block_type", "x", "y", "z"),
    ),
]

TOOL_NAMES = {tool["function"]["name"] for tool in TOOLS}
PROPOSED_ACTION_MARKERS = (
    "action:",
    "**action",
    "i will ",
    "let's ",
    "follow these steps",
    "gather materials",
    "let me know",
    "next step",
    "open the crafting table",
    "use `",
    "we need to ",
    "would you like",
)


def _proposes_future_action(content):
    lowered = content.lower()
    return any(marker in lowered for marker in PROPOSED_ACTION_MARKERS)


class BotToolError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class BotWebSocketClient:
    def __init__(self, url="ws://127.0.0.1:8765", timeout=300.0):
        self.url = url
        self.timeout = timeout
        self._connection = None
        self._request_ids = count(1)

    def connect(self):
        if self._connection is not None:
            raise RuntimeError("bot client is already connected")
        self._connection = connect(self.url, open_timeout=self.timeout)
        logger.info("connected planner to bot at %s", self.url)
        return self

    def close(self):
        if self._connection is None:
            return False
        self._connection.close()
        self._connection = None
        return True

    def __enter__(self):
        return self.connect()

    def __exit__(self, *_):
        self.close()

    def request(self, method, params=None):
        if self._connection is None:
            raise RuntimeError("bot client is not connected")
        request_id = f"planner-{next(self._request_ids)}"
        payload = {
            "version": 1,
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        self._connection.send(json.dumps(payload))
        response = json.loads(self._connection.recv(timeout=self.timeout))
        if response.get("id") != request_id:
            raise RuntimeError("bot response id did not match request")
        if not response.get("ok"):
            error = response.get("error", {})
            raise BotToolError(
                error.get("code", "UNKNOWN_ERROR"),
                error.get("message", "bot request failed"),
            )
        return response.get("result")


class OllamaClient:
    def __init__(self, model, base_url="http://127.0.0.1:11434", timeout=300.0):
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def chat(self, messages, tools):
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "stream": False,
                "think": False,
                "options": {
                    "temperature": 0.1,
                    "num_ctx": 8192,
                    "num_predict": 512,
                },
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama returned HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"could not connect to Ollama: {error.reason}") from error
        except TimeoutError as error:
            raise RuntimeError(
                f"Ollama did not respond within {self.timeout:g} seconds"
            ) from error
        message = result.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("Ollama response did not contain a message")
        return message


class Planner:
    def __init__(self, bot, ollama, max_steps=30, tools=TOOLS):
        if max_steps <= 0:
            raise ValueError("max_steps must be positive")
        self.bot = bot
        self.ollama = ollama
        self.max_steps = max_steps
        self.tools = tools
        self.tool_names = {tool["function"]["name"] for tool in tools}

    def run(self, goal):
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must be a non-empty string")

        initial_state = self.bot.request("observe")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Goal: {goal}\n\nInitial state:\n"
                    f"{json.dumps(initial_state, separators=(',', ':'))}"
                ),
            },
        ]
        consecutive_invalid_responses = 0
        consecutive_identical_failures = 0
        last_failure = None
        executed_tool_calls = 0
        executed_tool_names = []
        tree_workflow_required = any(
            word.strip(".,!?;:").lower() == "tree" for word in goal.split()
        )
        tree_workflow_attempted = False
        last_tool_name = None
        last_tool_result = None

        for step in range(1, self.max_steps + 1):
            logger.info("planner step %d/%d", step, self.max_steps)
            message = self.ollama.chat(messages, self.tools)
            messages.append(message)
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                content = message.get("content", "").strip()
                status = next(
                    (
                        prefix
                        for prefix in ("DONE:", "BLOCKED:")
                        if content.upper().startswith(prefix)
                    ),
                    None,
                )
                final_content = content[len(status):].strip() if status else ""
                exploration_only_blocker = (
                    status == "BLOCKED:"
                    and executed_tool_names
                    and set(executed_tool_names)
                    <= {"find_nearest_block", "find_nearest_blocks", "wander"}
                )
                missing_tree_workflow = (
                    status == "BLOCKED:"
                    and tree_workflow_required
                    and not tree_workflow_attempted
                )
                failed_wander_blocker = (
                    status == "BLOCKED:"
                    and last_tool_name == "wander"
                    and isinstance(last_tool_result, dict)
                    and not last_tool_result.get("ok", False)
                )
                if (
                    exploration_only_blocker
                    or missing_tree_workflow
                    or failed_wander_blocker
                ):
                    consecutive_invalid_responses += 1
                    if consecutive_invalid_responses >= 3:
                        blocked = (
                            "BLOCKED: Planner did not follow recovery instructions after "
                            "3 consecutive responses. Goal not verified. "
                            f"Last model response (unverified): {content}"
                        )
                        logger.warning("%s", blocked)
                        return blocked
                    logger.warning(
                        "planner declared BLOCKED without a terminal workflow result; "
                        "requesting a concrete workflow"
                    )
                    if tree_workflow_required and not tree_workflow_attempted:
                        retry = (
                            "You have not attempted the required tree workflow. Invoke "
                            "mine_nearest_tree with radius 128 now; it searches every "
                            "log variant. A failed wander is not a terminal blocker."
                        )
                    else:
                        retry = (
                            "Searching, wandering, or one failed wander does not establish "
                            "a terminal blocker. Invoke a concrete collection workflow or "
                            "try a different movement action now."
                        )
                    messages.append(
                        {
                            "role": "user",
                            "content": retry,
                        }
                    )
                    continue
                if status and final_content and executed_tool_calls:
                    logger.info("planner completed goal: %s", content)
                    return final_content
                if (
                    content
                    and executed_tool_calls
                    and content.upper() not in {"DONE", "BLOCKED"}
                    and not _proposes_future_action(content)
                ):
                    logger.warning(
                        "planner returned an unmarked conclusion; accepting it: %.500s",
                        content,
                    )
                    return content

                consecutive_invalid_responses += 1
                if consecutive_invalid_responses >= 3:
                    detail = content or "empty response"
                    blocked = (
                        "Planner could not produce an executable tool call after 3 "
                        f"attempts. Last response: {detail}"
                    )
                    logger.error("%s", blocked)
                    return blocked
                if content:
                    logger.warning(
                        "planner proposed an action without calling a tool; requesting "
                        "a retry: %.500s",
                        content,
                    )
                    retry = (
                        "Do not describe, recommend, or ask the user to perform the next "
                        "action. Invoke exactly one available tool now. Only respond with "
                        "DONE: after verification, or BLOCKED: followed by a concrete "
                        "blocker when no tool can make further progress."
                    )
                else:
                    logger.warning(
                        "planner returned an empty response; requesting a retry"
                    )
                    retry = (
                        "Your previous response was empty. Continue toward the goal with "
                        "exactly one valid tool call, or respond with BLOCKED: followed "
                        "by the concrete blocker if no tool can make further progress."
                    )
                messages.append({"role": "user", "content": retry})
                continue

            consecutive_invalid_responses = 0
            for call in tool_calls:
                function = call.get("function", {})
                name = function.get("name")
                arguments = function.get("arguments") or {}
                result = self._execute_tool(name, arguments)
                executed_tool_calls += 1
                executed_tool_names.append(name)
                if name == "mine_nearest_tree":
                    tree_workflow_attempted = True
                last_tool_name = name
                last_tool_result = result
                messages.append(
                    {
                        "role": "tool",
                        "tool_name": name or "unknown",
                        "content": json.dumps(result, separators=(",", ":")),
                    }
                )
                if not result["ok"]:
                    failure = (name, arguments, result["error"])
                    consecutive_identical_failures = (
                        consecutive_identical_failures + 1
                        if failure == last_failure else 1
                    )
                    last_failure = failure
                    if consecutive_identical_failures >= 3:
                        blocked = (
                            "BLOCKED: Planner stopped after 3 consecutive identical "
                            "tool failures. Goal not verified. "
                            f"Last tool: {name}. Arguments: "
                            f"{json.dumps(arguments, separators=(',', ':'))}. Result: "
                            f"{json.dumps(result, separators=(',', ':'))}"
                        )
                        logger.warning("%s", blocked)
                        return blocked
                else:
                    consecutive_identical_failures = 0
                    last_failure = None

        blocked = (
            f"BLOCKED: Planner reached its {self.max_steps}-step limit after "
            f"{executed_tool_calls} tool calls. Goal not verified."
        )
        if last_tool_name is not None:
            blocked += (
                f" Last tool: {last_tool_name}. Result: "
                f"{json.dumps(last_tool_result, separators=(',', ':'))}"
            )
        logger.warning("%s", blocked)
        return blocked

    def _execute_tool(self, name, arguments):
        if name not in self.tool_names:
            result = {"ok": False, "error": {"code": "UNKNOWN_TOOL", "message": str(name)}}
            logger.warning("planner requested unknown tool %s", name)
            return result
        if not isinstance(arguments, dict):
            return {
                "ok": False,
                "error": {
                    "code": "INVALID_ARGUMENTS",
                    "message": "tool arguments must be an object",
                },
            }

        logger.info("planner tool=%s arguments=%s", name, arguments)
        try:
            value = self.bot.request(name, arguments)
        except BotToolError as error:
            logger.warning("planner tool=%s failed: %s: %s", name, error.code, error)
            return {
                "ok": False,
                "error": {"code": error.code, "message": str(error)},
            }
        result = {"ok": True, "result": value}
        if name == "find_nearest_block":
            result["found"] = value is not None
            logger.info("planner tool=%s succeeded found=%s", name, result["found"])
        elif name == "find_nearest_blocks":
            result["count"] = len(value)
            logger.info("planner tool=%s succeeded count=%d", name, result["count"])
        elif name in {"collect_resource", "mine_nearest_tree"} and isinstance(value, dict):
            requested = value.get("requested")
            mined = value.get("mined")
            if name == "mine_nearest_tree" and isinstance(value.get("completed"), bool):
                result["completed"] = value["completed"]
                logger.info(
                    "planner tool=%s succeeded completed=%s logs_mined=%s logs_found=%s",
                    name,
                    result["completed"],
                    value.get("logs_mined"),
                    value.get("logs_found"),
                )
            elif isinstance(requested, int) and isinstance(mined, int):
                result["completed"] = mined >= requested
                if not result["completed"]:
                    result["reason"] = "NO_MATCH" if mined == 0 else "PARTIAL"
                logger.info(
                    "planner tool=%s succeeded completed=%s mined=%d requested=%d",
                    name,
                    result["completed"],
                    mined,
                    requested,
                )
            else:
                logger.info("planner tool=%s succeeded", name)
        else:
            logger.info("planner tool=%s succeeded", name)
        return result
