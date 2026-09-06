# WebSocket Protocol

The client and bot exchange JSON text messages over one WebSocket connection.
Protocol version `1` supports request/response messages only.

The Python server binds to `127.0.0.1:8765` by default. Run it from `bot` with
`python -m src.server`. Override connection settings with `WEBSOCKET_HOST`,
`WEBSOCKET_PORT`, and the `MINECRAFT_*` environment variables.

## Request

```json
{
  "version": 1,
  "id": "req-1",
  "method": "get_status",
  "params": {}
}
```

- `version` must be `1`.
- `id` must be a non-empty string and is echoed in the response.
- `method` must be one of the methods listed below.
- `params` must be an object. It may be omitted when the method has no parameters.

## Success Response

```json
{
  "version": 1,
  "id": "req-1",
  "ok": true,
  "result": {
    "connected": true,
    "spawned": true,
    "action": "idle"
  }
}
```

## Error Response

```json
{
  "version": 1,
  "id": "req-1",
  "ok": false,
  "error": {
    "code": "INVALID_PARAMS",
    "message": "quantity must be a positive integer"
  }
}
```

If a request cannot be decoded or has no valid ID, the response uses `null` for
`id`.

Error codes:

- `INVALID_REQUEST`: The message is not valid JSON or does not match the request envelope.
- `UNSUPPORTED_VERSION`: `version` is not supported.
- `METHOD_NOT_FOUND`: `method` is unknown.
- `INVALID_PARAMS`: Method parameters are missing or invalid.
- `BOT_ERROR`: The bot could not complete the operation.
- `INTERNAL_ERROR`: An unexpected server failure occurred.

## Methods

### `ping`

Parameters: none.

```json
{"pong": true}
```

### `disconnect`

Parameters: none. The server sends the response below, then closes that
WebSocket connection normally with close code `1000`. The Minecraft bot and
WebSocket server continue running.

```json
{"disconnected": true}
```

### `get_status`

Parameters: none. Returns `Bot.get_status()`.

### `observe`

Parameters: none. Returns `Bot.observe()`.

### Inventory and world queries

| Method | Parameters | Result |
| --- | --- | --- |
| `get_inventory` | None | Occupied inventory slots |
| `get_inventory_counts` | None | Item counts keyed by item name |
| `find_inventory_items` | `item_name` (string) | Matching inventory slots |
| `scan_nearby` | `radius` (positive integer) | Nearby entities and sampled block counts |
| `get_dropped_items` | `radius` (positive integer, default `8`) | Nearby dropped-item entities |
| `find_blocks` | `block_type`, `radius`, optional `count` (default `64`) | Matching block coordinates |
| `find_nearest_block` | `block_type`, `radius` | Nearest coordinate or `null` |
| `find_nearest_blocks` | `block_types` (non-empty string list), `radius` | Matching blocks ordered by distance |

### Looking and movement

Coordinate parameters `x`, `y`, and `z` must be finite numbers. Entity IDs must
be integers. Successful commands without a natural return value produce
`{"completed": true}`.

| Method | Parameters |
| --- | --- |
| `look_at` | `x`, `y`, `z` |
| `look_at_block` | `x`, `y`, `z` |
| `look_at_entity` | `entity_id` |
| `walk_forward` | Optional positive `duration` (default `1.0`) |
| `walk_backward` | Optional positive `duration` (default `1.0`) |
| `strafe_left` | Optional positive `duration` (default `1.0`) |
| `strafe_right` | Optional positive `duration` (default `1.0`) |
| `jump` | None |
| `stop_movement` | None |
| `move_to` | `x`, `y`, `z` |
| `move_near` | `x`, `y`, `z`, optional positive `distance` (default `1.5`) |
| `move_to_block` | `x`, `y`, `z` |
| `move_to_entity` | `entity_id`, optional positive `distance` (default `2.0`) |
| `follow_entity` | `entity_id`, optional positive `distance` (default `3.0`) |
| `stop_navigation` | None |
| `recalculate_path` | None; returns the path or `null` |

Navigation timeout, stuck, and cancellation failures return `BOT_ERROR`.

### Actions

| Method | Parameters | Result |
| --- | --- | --- |
| `mine_block` | `x`, `y`, `z` | `{"mined": true}` on success |
| `place_block` | `block_type`, `x`, `y`, `z`, optional positive `timeout` (default `3.0`) | Placed block type and position |
| `equip_best_tool_for_block` | `block_type` | Equipped inventory item or `null` |
| `collect_dropped_items` | Optional positive `timeout` (default `5.0`) and `radius` (default `8`) | Collected entity IDs |
| `send_chat` | `message` (1-256 characters) | `{"sent": true}` |

### `get_recipes`

```json
{
  "item_name": "wooden_pickaxe"
}
```

- `item_name` is required and must be a non-empty string.

Returns a list of recipe summaries for that item against the current inventory.
Each summary has `result_name`, `result_count`, `requires_crafting_table`,
`ingredients` (each with `name`, `required`, `in_inventory`, `missing`), and
`crafts_possible` (how many crafts the current inventory supports).

### `craft_item`

```json
{
  "item_name": "wooden_pickaxe",
  "quantity": 1
}
```

- `item_name` is required and must be a non-empty string.
- `quantity` is optional, defaults to `1`, and must be a positive integer.

Crafts the item, locating a nearby crafting table or placing one from inventory
when the recipe requires it. Returns `item_name`, `crafted`, and
`inventory_count`.

### `eat`

```json
{
  "item_name": "apple"
}
```

- `item_name` is optional. When omitted, the bot eats the highest-quality food
  in its inventory (ranked by minecraft-data `effectiveQuality`).

Equips and consumes one food item. Returns `eaten` (the item name) and the
resulting `food_level`. Fails with `BOT_ERROR` when no food is available, the
named item is not food, or the bot is already full.

### `collect_resource`

```json
{
  "block_type": "oak_log",
  "quantity": 3,
  "radius": 32,
  "timeout": 5.0
}
```

- `block_type` is required and must be a non-empty string.
- `quantity` is required and must be a positive integer.
- `radius` is optional, defaults to `32`, and must be a positive integer.
- `timeout` is optional, defaults to `5.0`, and must be a positive number.

Returns `Bot.collect_resource()`.

Unknown parameters are rejected with `INVALID_PARAMS` for every method.

## Correlation

Each request receives exactly one response. Clients may send multiple requests,
and must correlate responses by `id`; response order is not guaranteed.
