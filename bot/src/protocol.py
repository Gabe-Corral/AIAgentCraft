import json
import logging
import math

from javascript.errors import JavaScriptError

from .bot import UnknownBlockTypeError


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

PROTOCOL_VERSION = 1


class ProtocolError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _javascript_error_message(error):
    stack = error.js if isinstance(error.js, list) else str(error.js).splitlines()
    message = next((str(line).strip() for line in stack if str(line).strip()), "")
    return message or f"JavaScript call {error.call} failed"


def _success(request_id, result):
    return {
        "version": PROTOCOL_VERSION,
        "id": request_id,
        "ok": True,
        "result": result,
    }


def _error(request_id, code, message):
    return {
        "version": PROTOCOL_VERSION,
        "id": request_id,
        "ok": False,
        "error": {"code": code, "message": message},
    }


def _number(params, name, default=None, integer=False, positive=False):
    value = params.get(name, default)
    expected_type = int if integer else (int, float)
    if (
        isinstance(value, bool)
        or not isinstance(value, expected_type)
        or (isinstance(value, float) and not math.isfinite(value))
        or (positive and value <= 0)
    ):
        kind = "integer" if integer else "number"
        qualifier = "positive " if positive else "finite "
        raise ProtocolError("INVALID_PARAMS", f"{name} must be a {qualifier}{kind}")
    return value


def _positive_number(params, name, default=None, integer=False):
    return _number(params, name, default=default, integer=integer, positive=True)


def _string(params, name):
    value = params.get(name)
    if not isinstance(value, str) or not value:
        raise ProtocolError("INVALID_PARAMS", f"{name} must be a non-empty string")
    return value


def _string_list(params, name):
    value = params.get(name)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise ProtocolError(
            "INVALID_PARAMS", f"{name} must be a non-empty list of strings"
        )
    return value


def _reject_unknown(params, allowed):
    unknown = set(params) - set(allowed)
    if unknown:
        raise ProtocolError(
            "INVALID_PARAMS", f"unknown parameter: {sorted(unknown)[0]}"
        )


def _coordinates(params, extra=()):
    _reject_unknown(params, {"x", "y", "z", *extra})
    return tuple(_number(params, axis) for axis in ("x", "y", "z"))


def _completed(function, *args, **kwargs):
    function(*args, **kwargs)
    return {"completed": True}


def _dispatch(bot, method, params):
    if method == "ping":
        if params:
            raise ProtocolError("INVALID_PARAMS", "ping does not accept parameters")
        return {"pong": True}
    if method == "disconnect":
        if params:
            raise ProtocolError(
                "INVALID_PARAMS", "disconnect does not accept parameters"
            )
        return {"disconnected": True}
    if method == "get_status":
        if params:
            raise ProtocolError(
                "INVALID_PARAMS", "get_status does not accept parameters"
            )
        return bot.get_status()
    if method == "observe":
        if params:
            raise ProtocolError("INVALID_PARAMS", "observe does not accept parameters")
        return bot.observe()
    if method == "get_inventory":
        _reject_unknown(params, set())
        return bot.get_inventory()
    if method == "get_inventory_counts":
        _reject_unknown(params, set())
        return bot.get_inventory_counts()
    if method == "find_inventory_items":
        _reject_unknown(params, {"item_name"})
        return bot.find_inventory_items(_string(params, "item_name"))
    if method in {"scan_nearby", "get_dropped_items"}:
        _reject_unknown(params, {"radius"})
        default_radius = None if method == "scan_nearby" else 8
        radius = _positive_number(
            params, "radius", default=default_radius, integer=True
        )
        return getattr(bot, method)(radius)
    if method in {"find_blocks", "find_nearest_block"}:
        allowed = {"block_type", "radius"}
        if method == "find_blocks":
            allowed.add("count")
        _reject_unknown(params, allowed)
        block_type = _string(params, "block_type")
        radius = _positive_number(params, "radius", integer=True)
        if method == "find_blocks":
            count = _positive_number(params, "count", default=64, integer=True)
            return bot.find_blocks(block_type, radius, count)
        return bot.find_nearest_block(block_type, radius)
    if method == "find_nearest_blocks":
        _reject_unknown(params, {"block_types", "radius", "max_results"})
        block_types = _string_list(params, "block_types")
        radius = _positive_number(params, "radius", integer=True)
        if "max_results" in params:
            max_results = _positive_number(
                params, "max_results", integer=True
            )
            return bot.find_nearest_blocks(block_types, radius, max_results)
        return bot.find_nearest_blocks(block_types, radius)
    if method in {"look_at", "look_at_block", "move_to", "move_to_block"}:
        x, y, z = _coordinates(params)
        return _completed(getattr(bot, method), x, y, z)
    if method == "look_at_entity":
        _reject_unknown(params, {"entity_id"})
        entity_id = _number(params, "entity_id", integer=True)
        return _completed(bot.look_at_entity, entity_id)
    if method == "move_near":
        coordinates = _coordinates(params, {"distance"})
        distance = _positive_number(params, "distance", default=1.5)
        return _completed(bot.move_near, *coordinates, distance=distance)
    if method == "wander":
        _reject_unknown(params, {"radius"})
        radius = _positive_number(params, "radius")
        return _completed(bot.wander, radius)
    if method in {"move_to_entity", "follow_entity"}:
        _reject_unknown(params, {"entity_id", "distance"})
        entity_id = _number(params, "entity_id", integer=True)
        default_distance = 2.0 if method == "move_to_entity" else 3.0
        distance = _positive_number(params, "distance", default=default_distance)
        return _completed(getattr(bot, method), entity_id, distance=distance)
    if method in {"walk_forward", "walk_backward", "strafe_left", "strafe_right"}:
        _reject_unknown(params, {"duration"})
        duration = _positive_number(params, "duration", default=1.0)
        return _completed(getattr(bot, method), duration)
    if method in {"jump", "stop_movement", "stop_navigation"}:
        _reject_unknown(params, set())
        return _completed(getattr(bot, method))
    if method == "recalculate_path":
        _reject_unknown(params, set())
        return bot.recalculate_path()
    if method == "mine_block":
        x, y, z = _coordinates(params)
        return {"mined": bool(bot.mine_block(x, y, z))}
    if method == "place_block":
        coordinates = _coordinates(params, {"block_type", "timeout"})
        block_type = _string(params, "block_type")
        timeout = _positive_number(params, "timeout", default=3.0)
        return bot.place_block(block_type, *coordinates, timeout=timeout)
    if method == "equip_best_tool_for_block":
        _reject_unknown(params, {"block_type"})
        return bot.equip_best_tool_for_block(_string(params, "block_type"))
    if method == "collect_dropped_items":
        _reject_unknown(params, {"timeout", "radius"})
        timeout = _positive_number(params, "timeout", default=5.0)
        radius = _positive_number(params, "radius", default=8, integer=True)
        return bot.collect_dropped_items(timeout=timeout, radius=radius)
    if method == "send_chat":
        _reject_unknown(params, {"message"})
        message = _string(params, "message")
        if len(message) > 256:
            raise ProtocolError(
                "INVALID_PARAMS", "message must be at most 256 characters"
            )
        return {"sent": bool(bot.send_chat(message))}
    if method == "get_recipes":
        _reject_unknown(params, {"item_name"})
        return bot.get_recipes(_string(params, "item_name"))
    if method == "craft_item":
        _reject_unknown(params, {"item_name", "quantity"})
        quantity = _positive_number(params, "quantity", default=1, integer=True)
        return bot.craft_item(_string(params, "item_name"), quantity)
    if method == "eat":
        _reject_unknown(params, {"item_name"})
        item_name = params.get("item_name")
        if item_name is not None and (not isinstance(item_name, str) or not item_name):
            raise ProtocolError(
                "INVALID_PARAMS", "item_name must be a non-empty string"
            )
        return bot.eat(item_name)
    if method == "collect_resource":
        block_type = _string(params, "block_type")
        quantity = _positive_number(params, "quantity", integer=True)
        radius = _positive_number(params, "radius", default=32, integer=True)
        timeout = _positive_number(params, "timeout", default=5.0)
        _reject_unknown(params, {"block_type", "quantity", "radius", "timeout"})
        return bot.collect_resource(block_type, quantity, radius, timeout)
    if method == "mine_nearest_tree":
        _reject_unknown(params, {"radius", "max_logs", "timeout"})
        radius = _positive_number(params, "radius", default=64, integer=True)
        max_logs = _positive_number(params, "max_logs", default=64, integer=True)
        timeout = _positive_number(params, "timeout", default=5.0)
        return bot.mine_nearest_tree(radius, max_logs, timeout)
    raise ProtocolError("METHOD_NOT_FOUND", f"unknown method: {method}")


def handle_request(bot, message):
    """Decode one request and return one JSON response string."""
    request_id = None
    try:
        try:
            request = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            raise ProtocolError("INVALID_REQUEST", "message must be valid JSON")

        if not isinstance(request, dict):
            raise ProtocolError("INVALID_REQUEST", "request must be an object")

        candidate_id = request.get("id")
        if isinstance(candidate_id, str) and candidate_id:
            request_id = candidate_id
        else:
            raise ProtocolError("INVALID_REQUEST", "id must be a non-empty string")

        if request.get("version") != PROTOCOL_VERSION:
            raise ProtocolError("UNSUPPORTED_VERSION", "supported version is 1")

        method = request.get("method")
        if not isinstance(method, str) or not method:
            raise ProtocolError("INVALID_REQUEST", "method must be a non-empty string")

        params = request.get("params", {})
        if not isinstance(params, dict):
            raise ProtocolError("INVALID_PARAMS", "params must be an object")

        logger.info("request id=%s method=%s params=%s", request_id, method, params)
        try:
            result = _dispatch(bot, method, params)
        except ProtocolError:
            raise
        except UnknownBlockTypeError as error:
            raise ProtocolError("UNKNOWN_BLOCK_TYPE", str(error)) from error
        except JavaScriptError as error:
            raise ProtocolError("BOT_ERROR", _javascript_error_message(error)) from error
        except (KeyError, RuntimeError, TimeoutError, ValueError) as error:
            raise ProtocolError("BOT_ERROR", str(error)) from error
        except Exception as error:
            logger.exception(
                "request id=%s method=%s failed unexpectedly", request_id, method
            )
            raise ProtocolError("INTERNAL_ERROR", "unexpected server error") from error
        logger.info("request id=%s method=%s succeeded", request_id, method)
        return json.dumps(_success(request_id, result))
    except ProtocolError as error:
        logger.warning(
            "request id=%s failed: %s: %s", request_id, error.code, error
        )
        return json.dumps(_error(request_id, error.code, str(error)))
