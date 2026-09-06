import difflib
import heapq
import logging
import math
import random
import threading
import time

import lodestone


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


AIR_BLOCKS = {"air", "cave_air", "void_air"}
LOG_BLOCKS = (
    "oak_log",
    "spruce_log",
    "birch_log",
    "jungle_log",
    "acacia_log",
    "dark_oak_log",
    "mangrove_log",
    "cherry_log",
    "pale_oak_log",
)
LIQUID_BLOCKS = {
    "bubble_column",
    "flowing_lava",
    "flowing_water",
    "lava",
    "water",
}
REPLACEABLE_BLOCKS = AIR_BLOCKS | LIQUID_BLOCKS | {
    "dead_bush",
    "fern",
    "fire",
    "grass",
    "large_fern",
    "seagrass",
    "snow",
    "soul_fire",
    "tall_grass",
    "tall_seagrass",
    "vine",
}
HAZARDOUS_BLOCKS = {
    "cactus",
    "campfire",
    "fire",
    "flowing_lava",
    "lava",
    "magma_block",
    "pointed_dripstone",
    "powder_snow",
    "soul_campfire",
    "soul_fire",
    "sweet_berry_bush",
    "wither_rose",
}
MATERIAL_TO_TOOL = {
    "clay": "shovel",
    "dirt": "shovel",
    "gourd": "axe",
    "ice": "pickaxe",
    "leaves": "shears",
    "metal": "pickaxe",
    "rock": "pickaxe",
    "sand": "shovel",
    "snow": "shovel",
    "web": "shears",
    "wood": "axe",
    "wool": "shears",
}
TOOL_TIER_RANK = {
    "wooden": 1,
    "golden": 2,
    "stone": 3,
    "iron": 4,
    "diamond": 5,
    "netherite": 6,
}


class NavigationCancelledError(RuntimeError):
    pass


class UnknownBlockTypeError(ValueError):
    pass


class NavigationStuckError(RuntimeError):
    pass


class NavigationTimeoutError(TimeoutError):
    pass


class Bot:
    """Agent-friendly wrapper around a Lodestone/Mineflayer bot."""

    def __init__(
        self,
        host,
        auth,
        port,
        username,
        version,
        navigation_timeout=30.0,
        navigation_stuck_timeout=5.0,
        navigation_poll_interval=0.1,
        navigation_min_progress=0.1,
    ):
        navigation_settings = {
            "navigation_timeout": navigation_timeout,
            "navigation_stuck_timeout": navigation_stuck_timeout,
            "navigation_poll_interval": navigation_poll_interval,
            "navigation_min_progress": navigation_min_progress,
        }
        for name, value in navigation_settings.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{name} must be a positive number")

        self.host = host
        self.auth = auth
        self.port = port
        self.username = username
        self.version = version
        self.navigation_timeout = float(navigation_timeout)
        self.navigation_stuck_timeout = float(navigation_stuck_timeout)
        self.navigation_poll_interval = float(navigation_poll_interval)
        self.navigation_min_progress = float(navigation_min_progress)
        self.bot = None
        self._spawned = threading.Event()
        self._vec3_factory = None
        self._last_goal_position = None
        self._dig_thread = None
        self._dig_error = None
        self._digging_position = None
        self._craft_thread = None
        self._craft_error = None
        self._eat_thread = None
        self._eat_error = None
        self._recent_drop_ids = set()
        self._navigation_cancelled = threading.Event()

    def connect(self):
        """Connect using the stored connection settings."""
        if self.is_connected():
            raise RuntimeError("bot is already connected")

        logger.info(
            "connecting to %s:%s as %s (version %s)",
            self.host,
            self.port,
            self.username,
            self.version,
        )
        self._spawned.clear()
        self.bot = lodestone.createBot(
            host=self.host,
            auth=self.auth,
            port=self.port,
            username=self.username,
            version=self.version,
            ls_disable_viewer=True,
        )
        # Route planning must not excavate terrain autonomously. Resource
        # collection performs its own deliberate, verified block mining.
        self.bot.movements.canDig = False
        self.bot.movements.allow1by1towers = False

        @self.bot.on("spawn")
        def on_spawn(*_):
            logger.info("bot spawned")
            self._spawned.set()

        @self.bot.on("itemDrop")
        def on_item_drop(_, entity, *args):
            self._recent_drop_ids.add(int(entity.id))

        @self.bot.on("entityGone")
        def on_entity_gone(_, entity, *args):
            self._recent_drop_ids.discard(int(entity.id))

        if self._entity_or_none() is not None:
            self._spawned.set()

        return self

    def disconnect(self):
        """Disconnect from the server while preserving connection settings."""
        if self.bot is None:
            return False

        try:
            self.stop_movement()
        except Exception:
            pass
        try:
            self.stop_navigation()
        except Exception:
            pass
        try:
            self.cancel_breaking()
        except Exception:
            pass
        try:
            self.cancel_eating()
        except Exception:
            pass

        try:
            self.bot.stop()
        finally:
            self.bot = None
            self._spawned.clear()
            self._last_goal_position = None
        logger.info("bot disconnected")
        return True

    def reconnect(self):
        """Disconnect and reconnect using the current configuration."""
        self.disconnect()
        return self.connect()

    def is_connected(self):
        """Return whether a Lodestone bot instance is currently active."""
        return self.bot is not None

    def wait_until_spawned(self, timeout=30.0):
        """Wait until Mineflayer exposes the player's spawned entity."""
        self._require_bot()
        if self._entity_or_none() is not None:
            self._spawned.set()
        return self._spawned.wait(timeout)

    def respawn(self):
        """Request a respawn after death."""
        self._require_bot()
        self._spawned.clear()
        return self._client().respawn()

    def shutdown(self):
        """Stop current work and close the server connection."""
        return self.disconnect()

    # Internal helpers

    def _require_bot(self):
        if self.bot is None:
            raise RuntimeError("bot is not connected")
        return self.bot

    def _client(self):
        """Return the underlying Mineflayer JavaScript proxy."""
        return self._require_bot().bot

    def _entity_or_none(self):
        if self.bot is None:
            return None
        try:
            return self.bot.entity
        except Exception:
            return None

    def _vec3(self, x, y, z):
        if self._vec3_factory is None:
            from javascript import require

            self._vec3_factory = require("vec3")
        return self._vec3_factory(float(x), float(y), float(z))

    @staticmethod
    def _position_tuple(position):
        if isinstance(position, dict):
            return (position["x"], position["y"], position["z"])
        if isinstance(position, (tuple, list)):
            if len(position) != 3:
                raise ValueError("position must contain x, y, and z")
            return tuple(position)
        return (position.x, position.y, position.z)

    @staticmethod
    def _position_dict(position):
        x, y, z = Bot._position_tuple(position)
        return {"x": float(x), "y": float(y), "z": float(z)}

    def _block_at(self, x, y, z):
        return self._client().blockAt(self._vec3(math.floor(x), math.floor(y), math.floor(z)))

    def _block_position(self):
        x, y, z = self.get_position()
        return (math.floor(x), math.floor(y), math.floor(z))

    @staticmethod
    def _distance(a, b):
        ax, ay, az = Bot._position_tuple(a)
        bx, by, bz = Bot._position_tuple(b)
        return math.sqrt((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2)

    @staticmethod
    def _safe_call(function, default=None):
        try:
            return function()
        except Exception:
            return default

    @staticmethod
    def _proxy_values(sequence):
        """Convert a JavaScript array proxy or Python sequence to a list."""
        try:
            length = len(sequence.valueOf())
        except Exception:
            length = len(sequence)
        return [sequence[index] for index in range(length)]

    def _move_for(self, direction, duration):
        self._require_bot().set_control_state(direction, True)
        try:
            time.sleep(duration)
        finally:
            self.bot.set_control_state(direction, False)

    def walk_forward(self, duration=1.0):
        """Make the bot walk forward for the requested duration."""
        self._move_for("forward", duration)

    def walk_backward(self, duration=1.0):
        """Make the bot walk backward for the requested duration."""
        self._move_for("back", duration)

    def strafe_left(self, duration=1.0):
        """Make the bot strafe left for the requested duration."""
        self._move_for("left", duration)

    def strafe_right(self, duration=1.0):
        """Make the bot strafe right for the requested duration."""
        self._move_for("right", duration)

    def jump(self):
        """Make the bot jump once."""
        self._require_bot().set_control_state("jump", True)
        try:
            time.sleep(0.1)
        finally:
            self.bot.set_control_state("jump", False)

    def sprint(self, enable=True):
        """Enable or disable sprinting."""
        self._require_bot().set_control_state("sprint", enable)

    def sneak(self, enable=True):
        """Enable or disable sneaking."""
        self._require_bot().set_control_state("sneak", enable)

    def stop_movement(self):
        """Release all movement controls."""
        self._require_bot().clear_control_states()

    def _cleanup_navigation(self):
        bot = self._require_bot()
        try:
            # stop() defers cancellation and can poison the next goto().
            bot.pathfinder.setGoal(None)
        finally:
            bot.clear_control_states()

    def _navigate(self, goal):
        bot = self._require_bot()
        self._navigation_cancelled.clear()
        completed = threading.Event()
        result = {"error": None}

        def run_navigation():
            try:
                # Keep the bridge deadline just beyond our own so cleanup runs first.
                bot.pathfinder.goto(
                    goal,
                    timeout=self.navigation_timeout + max(
                        1.0, self.navigation_poll_interval
                    ),
                )
            except Exception as error:
                result["error"] = error
            finally:
                completed.set()

        started_at = time.monotonic()
        last_progress_at = started_at
        last_position = self.get_position()
        worker = threading.Thread(target=run_navigation, daemon=True)
        worker.start()

        try:
            while True:
                completed.wait(self.navigation_poll_interval)
                if self._navigation_cancelled.is_set():
                    raise NavigationCancelledError("navigation was cancelled")
                if completed.is_set():
                    break

                now = time.monotonic()
                if now - started_at >= self.navigation_timeout:
                    raise NavigationTimeoutError(
                        f"navigation timed out after {self.navigation_timeout:g} seconds"
                    )

                position = self.get_position()
                if self._distance(position, last_position) >= self.navigation_min_progress:
                    last_position = position
                    last_progress_at = now
                elif now - last_progress_at >= self.navigation_stuck_timeout:
                    raise NavigationStuckError(
                        "navigation is stuck: no position progress for "
                        f"{self.navigation_stuck_timeout:g} seconds"
                    )

            if result["error"] is not None:
                raise result["error"]
        except Exception:
            self._cleanup_navigation()
            completed.wait(self.navigation_poll_interval)
            raise

    def move_to(self, x, y, z):
        """Navigate to an exact position."""
        self._last_goal_position = (x, y, z)
        goal = self._require_bot().goals.GoalBlock(x, y, z)
        self._navigate(goal)

    def move_near(self, x, y, z, distance=1.5):
        """Navigate to within distance of a position."""
        self._last_goal_position = (x, y, z)
        if self.is_solid(x, y, z):
            return self.move_within_reach(x, y, z)
        goal = self._require_bot().goals.GoalNear(x, y, z, distance)
        self._navigate(goal)

    def move_to_block(self, x, y, z):
        """Navigate to a position adjacent to a block."""
        self._last_goal_position = (x, y, z)
        goal = self._require_bot().goals.GoalGetToBlock(x, y, z)
        self._navigate(goal)

    def _get_entity(self, entity_id):
        entity = self._require_bot().entities[entity_id]
        if entity is None:
            raise ValueError(f"Entity {entity_id!r} is not loaded")
        return entity

    def move_to_entity(self, entity_id, distance=2.0):
        """Navigate once to within distance of an entity."""
        entity = self._get_entity(entity_id)
        goal = self.bot.goals.GoalFollow(entity, distance)
        self._navigate(goal)

    def follow_entity(self, entity_id, distance=3.0):
        """Continuously follow an entity at the requested distance."""
        entity = self._get_entity(entity_id)
        goal = self.bot.goals.GoalFollow(entity, distance)
        self.bot.pathfinder.setGoal(goal, True)

    def move_away_from(self, x, y, z, distance):
        """Navigate outside distance of a position."""
        near_goal = self._require_bot().goals.GoalNear(x, y, z, distance)
        self._navigate(self.bot.goals.GoalInvert(near_goal))

    def wander(self, radius):
        """Navigate to a random nearby position within radius."""
        if radius < 0:
            raise ValueError("radius must be non-negative")

        position = self._require_bot().entity.position
        angle = random.uniform(0, 2 * math.pi)
        target_distance = radius * math.sqrt(random.random())
        self.move_near(
            position.x + math.cos(angle) * target_distance,
            position.y,
            position.z + math.sin(angle) * target_distance,
        )

    def stop_navigation(self):
        """Cancel the current pathfinder goal."""
        self._navigation_cancelled.set()
        self._require_bot().pathfinder.setGoal(None)

    def find_path(self, start, goal, max_nodes=2000):
        """Find a bounded block-grid A* path through currently loaded terrain."""
        start = tuple(math.floor(value) for value in self._position_tuple(start))
        goal = tuple(math.floor(value) for value in self._position_tuple(goal))
        frontier = [(self.estimate_path_cost(start, goal), 0, start)]
        came_from = {start: None}
        costs = {start: 0}
        expanded = 0

        while frontier and expanded < max_nodes:
            _, current_cost, current = heapq.heappop(frontier)
            if current_cost != costs.get(current):
                continue
            expanded += 1
            if current == goal:
                path = []
                while current is not None:
                    path.append(current)
                    current = came_from[current]
                return list(reversed(path))

            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                for dy in (0, 1, -1):
                    candidate = (
                        current[0] + dx,
                        current[1] + dy,
                        current[2] + dz,
                    )
                    if not self.can_move_between(current, candidate):
                        continue
                    next_cost = current_cost + (1.5 if dy else 1.0)
                    if next_cost >= costs.get(candidate, math.inf):
                        continue
                    costs[candidate] = next_cost
                    came_from[candidate] = current
                    priority = next_cost + self.estimate_path_cost(candidate, goal)
                    heapq.heappush(frontier, (priority, next_cost, candidate))

        return None

    def execute_path(self, path):
        """Navigate through a sequence of x/y/z waypoints."""
        if not path:
            raise ValueError("path must contain at least one waypoint")
        for waypoint in path[1:]:
            x, y, z = self._position_tuple(waypoint)
            self.move_near(x, y, z, distance=0.4)
        return True

    def recalculate_path(self):
        """Recalculate and execute a path to the last coordinate goal."""
        if self._last_goal_position is None:
            return None
        path = self.find_path(self._block_position(), self._last_goal_position)
        if path:
            self.execute_path(path)
        return path

    def is_position_walkable(self, x, y, z):
        """Return whether a player can stand at the supplied block position."""
        return (
            not self.is_solid(x, y, z)
            and not self.is_solid(x, y + 1, z)
            and self.is_solid(x, y - 1, z)
            and not self.is_hazardous(x, y, z)
            and not self.is_hazardous(x, y - 1, z)
        )

    def can_move_between(self, start, end):
        """Return whether one normal walking step can connect two positions."""
        sx, sy, sz = self._position_tuple(start)
        ex, ey, ez = self._position_tuple(end)
        if abs(ex - sx) + abs(ez - sz) != 1 or abs(ey - sy) > 1:
            return False
        if ey > sy and self.is_solid(sx, sy + 2, sz):
            return False
        return self.is_position_walkable(ex, ey, ez)

    def find_nearest_reachable_position(self, x, y, z, radius=8):
        """Find the nearest walkable block around a target coordinate."""
        origin = self._block_position()
        candidates = []
        for distance in range(radius + 1):
            for dx in range(-distance, distance + 1):
                for dz in range(-distance, distance + 1):
                    if max(abs(dx), abs(dz)) != distance:
                        continue
                    for dy in (0, 1, -1, 2, -2):
                        candidate = (math.floor(x + dx), math.floor(y + dy), math.floor(z + dz))
                        if self.is_position_walkable(*candidate):
                            candidates.append(candidate)
            if candidates:
                return min(candidates, key=lambda point: self._distance(origin, point))
        return None

    def find_safe_position(self, radius):
        """Find a nearby walkable position without adjacent hazards."""
        if radius < 0:
            raise ValueError("radius must be non-negative")
        x, y, z = self._block_position()
        candidates = []
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                candidate = (x + dx, y, z + dz)
                if not self.is_position_walkable(*candidate):
                    continue
                neighbors = (
                    (candidate[0] + ox, candidate[1], candidate[2] + oz)
                    for ox, oz in ((1, 0), (-1, 0), (0, 1), (0, -1))
                )
                if not any(self.is_hazardous(*neighbor) for neighbor in neighbors):
                    candidates.append(candidate)
        if not candidates:
            return None
        return min(candidates, key=lambda point: self._distance((x, y, z), point))

    def estimate_path_cost(self, start, goal):
        """Estimate movement cost with an added penalty for vertical travel."""
        sx, sy, sz = self._position_tuple(start)
        gx, gy, gz = self._position_tuple(goal)
        return abs(gx - sx) + abs(gz - sz) + 1.5 * abs(gy - sy)

    def get_position(self):
        """Return the current x/y/z position as a tuple."""
        entity = self._entity_or_none()
        if entity is None:
            raise RuntimeError("bot has not spawned")
        return self._position_tuple(entity.position)

    def get_rotation(self):
        """Return yaw and pitch in Mineflayer radians."""
        entity = self._entity_or_none()
        if entity is None:
            raise RuntimeError("bot has not spawned")
        return (float(entity.yaw), float(entity.pitch))

    def get_health(self):
        return float(self._require_bot().health)

    def get_food_level(self):
        return float(self._require_bot().food)

    def get_experience(self):
        experience = self._require_bot().experience
        return {
            "level": int(experience.level),
            "points": int(experience.points),
            "progress": float(experience.progress),
        }

    def get_game_mode(self):
        return str(self._require_bot().game.game_mode)

    def get_dimension(self):
        return str(self._require_bot().game.dimension)

    def get_world_time(self):
        current_time = self._require_bot().time
        return {
            "time": int(current_time.time),
            "time_of_day": int(current_time.time_of_day),
            "day": int(current_time.day),
        }

    def get_weather(self):
        bot = self._require_bot()
        if float(bot.thunder_state) > 0:
            return "thunderstorm"
        if bool(bot.is_raining):
            return "rain"
        return "clear"

    def get_current_action(self):
        if self._digging_position is not None:
            return "mining"
        if self._eat_thread is not None and self._eat_thread.is_alive():
            return "eating"
        if self.bot is None:
            return "disconnected"
        try:
            if self.bot.pathfinder.isMoving():
                return "moving"
        except Exception:
            pass
        return "idle"

    def get_status(self):
        status = {
            "connected": self.is_connected(),
            "spawned": self._spawned.is_set(),
            "action": self.get_current_action(),
        }
        if not self.is_connected():
            return status
        status.update(
            {
                "position": self._safe_call(lambda: self._position_dict(self.get_position())),
                "rotation": self._safe_call(
                    lambda: dict(zip(("yaw", "pitch"), self.get_rotation()))
                ),
                "health": self._safe_call(self.get_health),
                "food": self._safe_call(self.get_food_level),
                "game_mode": self._safe_call(self.get_game_mode),
                "dimension": self._safe_call(self.get_dimension),
            }
        )
        return status

    def look_at(self, x, y, z):
        """Look at an exact world coordinate."""
        return self._client().lookAt(self._vec3(x, y, z))

    def look_at_block(self, x, y, z):
        """Look at the center of a block."""
        return self.look_at(x + 0.5, y + 0.5, z + 0.5)

    def look_at_entity(self, entity_id):
        """Look at the current position of a loaded entity."""
        entity = self._get_entity(entity_id)
        position = entity.position
        height = float(getattr(entity, "height", 0))
        return self.look_at(position.x, position.y + height * 0.5, position.z)

    def set_yaw(self, yaw):
        _, pitch = self.get_rotation()
        return self._client().look(float(yaw), pitch)

    def set_pitch(self, pitch):
        yaw, _ = self.get_rotation()
        pitch = max(-math.pi / 2, min(math.pi / 2, float(pitch)))
        return self._client().look(yaw, pitch)

    def turn_left(self, degrees):
        yaw, _ = self.get_rotation()
        return self.set_yaw(yaw - math.radians(degrees))

    def turn_right(self, degrees):
        yaw, _ = self.get_rotation()
        return self.set_yaw(yaw + math.radians(degrees))

    def look_up(self, degrees):
        _, pitch = self.get_rotation()
        return self.set_pitch(pitch - math.radians(degrees))

    def look_down(self, degrees):
        _, pitch = self.get_rotation()
        return self.set_pitch(pitch + math.radians(degrees))

    def observe(self):
        """Return a JSON-friendly snapshot of the bot and nearby world."""
        observation = self.get_status()
        if not self.is_connected():
            return observation
        observation.update(
            {
                "experience": self._safe_call(self.get_experience),
                "world_time": self._safe_call(self.get_world_time),
                "weather": self._safe_call(self.get_weather),
                "biome": self._safe_call(self.get_biome),
                "daytime": self._safe_call(self.is_daytime),
                "light_level": self._safe_call(
                    lambda: self.get_light_level(*self.get_position())
                ),
                "inventory": self._safe_call(self.get_inventory, []),
                "nearby": self._safe_call(lambda: self.scan_nearby(8), {}),
            }
        )
        return observation

    def scan_nearby(self, radius):
        """Return nearby entities and sampled block counts."""
        if radius < 0:
            raise ValueError("radius must be non-negative")
        origin = self.get_position()
        entities = []
        for entity_id in self._entity_ids():
            try:
                entity = self.bot.entities[entity_id]
                distance = self._distance(origin, entity.position)
                if distance > radius:
                    continue
                entities.append(
                    {
                        "id": int(entity_id),
                        "name": str(
                            getattr(entity, "username", None)
                            or getattr(entity, "name", None)
                            or "unknown"
                        ),
                        "distance": distance,
                        "position": self._position_dict(entity.position),
                    }
                )
            except Exception:
                continue

        block_counts = {}
        for block in self.get_blocks_in_radius(radius, max_blocks=512):
            block_counts[block["name"]] = block_counts.get(block["name"], 0) + 1
        return {"entities": entities, "block_counts": block_counts}

    def get_block(self, x, y, z):
        """Return the raw prismarine-block proxy at a coordinate."""
        return self._block_at(x, y, z)

    def get_blocks_in_radius(self, radius, max_blocks=1024):
        """Sample loaded blocks around the bot, capped to avoid bridge overload."""
        if radius < 0:
            raise ValueError("radius must be non-negative")
        cx, cy, cz = self._block_position()
        diameter = 2 * radius + 1
        volume = diameter**3
        stride = max(1, math.ceil((volume / max_blocks) ** (1 / 3)))
        blocks = []
        for x in range(cx - radius, cx + radius + 1, stride):
            for y in range(cy - radius, cy + radius + 1, stride):
                for z in range(cz - radius, cz + radius + 1, stride):
                    block = self._block_at(x, y, z)
                    if block is None or str(block.name) in AIR_BLOCKS:
                        continue
                    blocks.append(
                        {"name": str(block.name), "x": x, "y": y, "z": z}
                    )
                    if len(blocks) >= max_blocks:
                        return blocks
        return blocks

    def _block_type(self, block_type):
        blocks_by_name = self._require_bot().registry.blocksByName
        try:
            block = blocks_by_name[block_type]
        except KeyError:
            block = None
        if not block:
            try:
                names = [str(name) for name in blocks_by_name.keys()]
            except (AttributeError, TypeError):
                names = []
            suggestions = difflib.get_close_matches(block_type, names, n=3, cutoff=0.5)
            message = f"unknown block type: {block_type}"
            if suggestions:
                message += f"; did you mean: {', '.join(suggestions)}"
            raise UnknownBlockTypeError(message)
        return block

    def find_blocks(self, block_type, radius, count=64):
        """Find matching loaded blocks ordered by Mineflayer distance."""
        if radius < 0:
            raise ValueError("radius must be non-negative")
        block = self._block_type(block_type)
        positions = self._client().findBlocks(
            {"matching": block.id, "maxDistance": radius, "count": count}
        )
        return [self._position_tuple(position) for position in self._proxy_values(positions)]

    def find_nearest_block(self, block_type, radius):
        blocks = self.find_blocks(block_type, radius, count=1)
        return blocks[0] if blocks else None

    def find_nearest_blocks(self, block_types, radius, max_results=16):
        if max_results <= 0:
            raise ValueError("max_results must be positive")
        origin = self.get_position()
        blocks = []
        # Reject the complete request before searching so invalid names cannot
        # discard partial results after doing work for valid names.
        for block_type in block_types:
            self._block_type(block_type)
        for block_type in block_types:
            for position in self.find_blocks(block_type, radius, count=max_results):
                blocks.append({"type": block_type, "position": position})
        blocks.sort(key=lambda result: self._distance(origin, result["position"]))
        return blocks[:max_results]

    def get_loaded_chunks(self):
        columns = self._proxy_values(self._require_bot().world.getColumns())
        return [
            {
                "x": int(column.chunkX),
                "z": int(column.chunkZ),
            }
            for column in columns
        ]

    def get_biome(self):
        biome_id = int(self._require_bot().world.getBiome(self._vec3(*self.get_position())))
        try:
            return str(self.bot.mc_data.biomes[biome_id].name)
        except Exception:
            return str(biome_id)

    def get_light_level(self, x, y, z):
        block = self._block_at(x, y, z)
        if block is None:
            return 0
        block_light = int(getattr(block, "light", 0) or 0)
        sky_light = int(getattr(block, "skyLight", 0) or 0)
        return max(block_light, sky_light)

    def is_daytime(self):
        return bool(self._require_bot().time.is_day)

    # Inventory

    @staticmethod
    def _inventory_item(item):
        return {
            "name": str(item.name),
            "display_name": str(getattr(item, "displayName", item.name)),
            "count": int(item.count),
            "slot": int(item.slot),
            "type": int(item.type),
            "metadata": int(getattr(item, "metadata", 0) or 0),
        }

    def _inventory_items(self):
        return self._proxy_values(self._client().inventory.items())

    def get_inventory(self):
        """Return occupied inventory slots as JSON-friendly dictionaries."""
        return [self._inventory_item(item) for item in self._inventory_items()]

    def get_inventory_counts(self):
        """Return total item counts grouped by item name."""
        counts = {}
        for item in self.get_inventory():
            counts[item["name"]] = counts.get(item["name"], 0) + item["count"]
        return counts

    def find_inventory_items(self, item_name):
        """Return inventory slots containing the requested item."""
        return [item for item in self.get_inventory() if item["name"] == item_name]

    # Crafting

    def _item_type(self, item_name):
        item = self._require_bot().registry.itemsByName[item_name]
        if not item:
            raise KeyError(f"unknown item type: {item_name}")
        return item

    def _item_name(self, item_id):
        item = self._require_bot().registry.items[int(item_id)]
        if not item:
            raise KeyError(f"unknown item id: {item_id}")
        return str(item.name)

    def _find_crafting_table_block(self, radius=16):
        """Return the block proxy for a nearby placed crafting table, if any."""
        try:
            block_type = self._block_type("crafting_table")
        except KeyError:
            return None
        positions = self._proxy_values(
            self._client().findBlocks(
                {"matching": block_type.id, "maxDistance": radius, "count": 1}
            )
        )
        if not positions:
            return None
        position = self._position_tuple(positions[0])
        return self._block_at(*position)

    def _has_inventory_item(self, item_name):
        return any(
            str(item.name) == item_name for item in self._inventory_items()
        )

    def _recipe_requirements(self, recipe):
        """Return the item names and counts consumed by one craft."""
        required = {}
        for delta in self._proxy_values(recipe.delta):
            count = int(delta.count)
            if count >= 0:
                continue
            name = self._safe_call(lambda: self._item_name(delta.id), None)
            if name is None:
                continue
            required[name] = required.get(name, 0) + (-count)
        return required

    def _describe_recipe(self, recipe, counts):
        """Convert one mineflayer recipe into a JSON-friendly summary."""
        result = recipe.result
        required = self._recipe_requirements(recipe)

        ingredients = []
        for name, need in required.items():
            have = int(counts.get(name, 0))
            ingredients.append(
                {
                    "name": name,
                    "required": need,
                    "in_inventory": have,
                    "missing": max(0, need - have),
                }
            )

        crafts_possible = 0
        if required:
            crafts_possible = min(
                counts.get(name, 0) // need for name, need in required.items()
            )

        return {
            "result_name": self._safe_call(lambda: self._item_name(result.id), None),
            "result_count": int(result.count),
            "requires_crafting_table": bool(recipe.requiresTable),
            "ingredients": ingredients,
            "crafts_possible": int(crafts_possible),
        }

    def get_recipes(self, item_name):
        """Return recipe summaries for an item against the current inventory."""
        if not isinstance(item_name, str) or not item_name:
            raise ValueError("item_name must be a non-empty string")
        item = self._item_type(item_name)
        counts = self.get_inventory_counts()
        recipes = self._proxy_values(
            self._client().recipesAll(item.id, None, None)
        )
        return [self._describe_recipe(recipe, counts) for recipe in recipes]

    def craft_item(self, item_name, quantity=1):
        """Craft an item, locating or placing a crafting table when required."""
        if not isinstance(item_name, str) or not item_name:
            raise ValueError("item_name must be a non-empty string")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise ValueError("quantity must be a positive integer")

        item = self._item_type(item_name)
        counts = self.get_inventory_counts()
        before = int(counts.get(item_name, 0))
        logger.info("crafting %d %s", quantity, item_name)

        table_block = self._find_crafting_table_block()
        recipes = self._proxy_values(
            self._client().recipesAll(item.id, None, table_block)
        )
        recipe = self._select_craftable_recipe(recipes, counts, quantity)

        # Some recipes are only visible once a crafting table is available.
        if (
            recipe is None
            and table_block is None
            and self._has_inventory_item("crafting_table")
        ):
            table_block = self._place_crafting_table()
            recipes = self._proxy_values(
                self._client().recipesAll(item.id, None, table_block)
            )
            recipe = self._select_craftable_recipe(recipes, counts, quantity)

        if recipe is None:
            if not recipes:
                raise RuntimeError(f"no recipe available for {item_name}")
            missing = self._missing_ingredients(recipes[0], counts, quantity)
            raise RuntimeError(
                f"missing ingredients to craft {quantity} {item_name}: {missing}"
            )

        if bool(recipe.requiresTable):
            if table_block is None:
                raise RuntimeError(
                    f"{item_name} requires a crafting table but none is available"
                )
            position = self._position_tuple(table_block.position)
            self.move_within_reach(*position)
            table_block = self._block_at(*position)

        self.start_crafting(recipe, quantity, table_block)
        self.finish_crafting()
        after = int(self.get_inventory_counts().get(item_name, 0))
        logger.info("crafted %s: inventory count is now %d", item_name, after)
        return {
            "item_name": item_name,
            "crafted": max(0, after - before),
            "inventory_count": after,
        }

    def start_crafting(self, recipe, quantity, crafting_table=None):
        """Start an async craft in a background thread.

        ``bot.craft`` awaits window clicks, so calling it synchronously from
        the main thread blocks the JS bridge and times out (the same reason
        ``dig`` runs in a thread).
        """
        if self._craft_thread is not None and self._craft_thread.is_alive():
            raise RuntimeError("already crafting")
        self._craft_error = None

        def craft():
            try:
                self._client().craft(recipe, quantity, crafting_table)
            except Exception as error:
                self._craft_error = error

        self._craft_thread = threading.Thread(target=craft, daemon=True)
        self._craft_thread.start()
        return self._craft_thread

    def finish_crafting(self, timeout=30.0):
        """Wait for the active craft to finish and re-raise any failure."""
        if self._craft_thread is None:
            return True
        self._craft_thread.join(timeout)
        if self._craft_thread.is_alive():
            raise TimeoutError("crafting did not finish before timeout")
        error = self._craft_error
        self._craft_thread = None
        self._craft_error = None
        if error is not None:
            raise error
        return True

    # Eating

    def _food_stats(self):
        """Map inventory food item names to their minecraft-data food stats."""
        stats = {}
        for food in self._proxy_values(self._require_bot().mc_data.foods):
            name = self._safe_call(lambda: str(food.name), None)
            if name is not None:
                stats[name] = {
                    "food_points": float(getattr(food, "foodPoints", 0) or 0),
                    "saturation": float(getattr(food, "saturation", 0) or 0),
                    "effective_quality": float(
                        getattr(food, "effectiveQuality", 0) or 0
                    ),
                }
        return stats

    def _best_food_item(self):
        """Return the highest-quality food item currently in the inventory."""
        stats = self._food_stats()
        candidates = [
            (stats[str(item.name)]["effective_quality"], item)
            for item in self._inventory_items()
            if str(item.name) in stats
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda candidate: candidate[0])[1]

    def start_eating(self, item):
        """Equip and consume an item in a background thread.

        ``bot.consume`` awaits an eating task, so like ``dig`` and ``craft``
        it must not block the JS bridge on the main thread.
        """
        if self._eat_thread is not None and self._eat_thread.is_alive():
            raise RuntimeError("already eating")
        self._eat_error = None

        def consume():
            try:
                client = self._client()
                client.equip(item, "hand")
                client.consume()
            except Exception as error:
                self._eat_error = error

        self._eat_thread = threading.Thread(target=consume, daemon=True)
        self._eat_thread.start()
        return self._eat_thread

    def finish_eating(self, timeout=10.0):
        """Wait for the active consume to finish and re-raise any failure."""
        if self._eat_thread is None:
            return True
        self._eat_thread.join(timeout)
        if self._eat_thread.is_alive():
            raise TimeoutError("eating did not finish before timeout")
        error = self._eat_error
        self._eat_thread = None
        self._eat_error = None
        if error is not None:
            raise error
        return True

    def cancel_eating(self):
        """Abandon the active consume attempt and deactivate the held item."""
        if self.bot is None:
            return False
        self._eat_thread = None
        self._eat_error = None
        self._safe_call(lambda: self._client().deactivateItem())
        return True

    def eat(self, item_name=None):
        """Eat one food item, choosing the best available when not specified."""
        if item_name is not None and (not isinstance(item_name, str) or not item_name):
            raise ValueError("item_name must be a non-empty string")

        if item_name is None:
            item = self._best_food_item()
            if item is None:
                raise RuntimeError("no food available in inventory")
        else:
            item = next(
                (
                    inventory_item
                    for inventory_item in self._inventory_items()
                    if str(inventory_item.name) == item_name
                ),
                None,
            )
            if item is None:
                raise RuntimeError(f"no {item_name} available in inventory")
            if item_name not in self._food_stats():
                raise RuntimeError(f"{item_name} is not a food item")

        eaten_name = str(item.name)
        logger.info("eating %s (food level %.0f)", eaten_name, self.get_food_level())
        self.start_eating(item)
        try:
            self.finish_eating()
        except Exception:
            self.cancel_eating()
            raise
        logger.info("ate %s (food level %.0f)", eaten_name, self.get_food_level())
        return {"eaten": eaten_name, "food_level": self.get_food_level()}

    def _select_craftable_recipe(self, recipes, counts, quantity):
        """Return the first recipe the current inventory can fully supply."""
        for recipe in recipes:
            required = self._recipe_requirements(recipe)
            if all(
                counts.get(name, 0) >= need * quantity
                for name, need in required.items()
            ):
                return recipe
        return None

    def _missing_ingredients(self, recipe, counts, quantity):
        """Summarize what the inventory lacks for the requested crafts."""
        missing = {}
        for name, need in self._recipe_requirements(recipe).items():
            shortfall = need * quantity - counts.get(name, 0)
            if shortfall > 0:
                missing[name] = shortfall
        return missing

    def _place_crafting_table(self):
        """Place a crafting table from inventory on a nearby solid block."""
        table_item = next(
            (
                inventory_item
                for inventory_item in self._inventory_items()
                if str(inventory_item.name) == "crafting_table"
            ),
            None,
        )
        if table_item is None:
            raise RuntimeError(
                "recipe requires a crafting table but none is nearby or in inventory"
            )

        x, y, z = self._block_position()
        for dx in range(-3, 4):
            for dz in range(-3, 4):
                candidate = (x + dx, y - 1, z + dz)
                if self.is_solid(*candidate):
                    above = (candidate[0], candidate[1] + 1, candidate[2])
                    if self.is_replaceable(*above):
                        self.place_block(
                            "crafting_table", above[0], above[1], above[2]
                        )
                        return self._block_at(*above)
        raise RuntimeError("no suitable location to place a crafting table")

    def is_air(self, x, y, z):
        block = self._block_at(x, y, z)
        return block is None or str(block.name) in AIR_BLOCKS

    def is_solid(self, x, y, z):
        block = self._block_at(x, y, z)
        return block is not None and str(block.boundingBox) == "block"

    def is_liquid(self, x, y, z):
        block = self._block_at(x, y, z)
        return block is not None and str(block.name) in LIQUID_BLOCKS

    def is_replaceable(self, x, y, z):
        block = self._block_at(x, y, z)
        return block is None or str(block.name) in REPLACEABLE_BLOCKS

    def is_breakable(self, x, y, z):
        block = self._block_at(x, y, z)
        return block is not None and bool(block.diggable) and str(block.name) not in AIR_BLOCKS

    def is_hazardous(self, x, y, z):
        block = self._block_at(x, y, z)
        return block is not None and str(block.name) in HAZARDOUS_BLOCKS

    def is_block_reachable(self, x, y, z):
        px, py, pz = self.get_position()
        eye_position = (px, py + 1.62, pz)
        block_center = (x + 0.5, y + 0.5, z + 0.5)
        return self._distance(eye_position, block_center) <= 4.5

    def get_block_hardness(self, x, y, z):
        block = self._block_at(x, y, z)
        if block is None:
            return 0.0
        hardness = getattr(block, "hardness", None)
        return math.inf if hardness is None else float(hardness)

    def get_required_tool(self, block_type):
        """Return the tool class needed for drops, or None for hand-harvestable blocks."""
        block = self._block_type(block_type)
        harvest_tools = getattr(block, "harvestTools", None)
        if not harvest_tools:
            return None
        return MATERIAL_TO_TOOL.get(str(getattr(block, "material", "")))

    def get_best_tool_for_block(self, block_type):
        """Return the fastest common tool class for a block material."""
        block = self._block_type(block_type)
        return MATERIAL_TO_TOOL.get(str(getattr(block, "material", "")), "hand")

    @staticmethod
    def _tool_rank(item_name, tool_type):
        if tool_type == "shears":
            return 1 if item_name == "shears" else -1
        suffix = f"_{tool_type}"
        if not item_name.endswith(suffix):
            return -1
        return TOOL_TIER_RANK.get(item_name.removesuffix(suffix), 0)

    @staticmethod
    def _harvest_tool_ids(block):
        harvest_tools = getattr(block, "harvestTools", None)
        if not harvest_tools:
            return None
        try:
            harvest_tools = harvest_tools.valueOf()
        except Exception:
            pass
        try:
            return {int(item_id) for item_id in harvest_tools.keys()}
        except Exception:
            return None

    def equip_best_tool_for_block(self, block_type):
        """Equip the strongest compatible inventory tool for a block."""
        block = self._block_type(block_type)
        tool_type = self.get_best_tool_for_block(block_type)
        if tool_type == "hand":
            return None

        harvest_tool_ids = self._harvest_tool_ids(block)
        candidates = []
        for item in self._inventory_items():
            rank = self._tool_rank(str(item.name), tool_type)
            if rank < 0:
                continue
            if harvest_tool_ids is not None and int(item.type) not in harvest_tool_ids:
                continue
            candidates.append((rank, item))

        if not candidates:
            return None
        _, item = max(candidates, key=lambda candidate: candidate[0])
        self._client().equip(item, "hand")
        return self._inventory_item(item)

    def move_within_reach(self, x, y, z):
        """Navigate close enough to a block to dig or place against it.

        Prefers ``GoalGetToBlock`` (stand on or directly adjacent). When the
        block is not directly reachable — for example a log partway up a tree
        trunk — falls back to navigating near the closest walkable position,
        which brings higher blocks within digging reach.
        """
        if self.is_block_reachable(x, y, z):
            return True
        try:
            self.move_to_block(x, y, z)
        except (NavigationStuckError, NavigationTimeoutError, NavigationCancelledError):
            raise
        except Exception:
            pass
        if self.is_block_reachable(x, y, z):
            return True

        position = self.find_nearest_reachable_position(x, y, z)
        if position is not None:
            self.move_near(*position, distance=1.5)
            if self.is_block_reachable(x, y, z):
                return True

        raise RuntimeError(f"could not move within reach of block at {(x, y, z)}")

    def place_block(self, block_type, x, y, z, timeout=3.0):
        """Equip and place one inventory block at a world coordinate."""
        if not isinstance(block_type, str) or not block_type:
            raise ValueError("block_type must be a non-empty string")
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        position = tuple(math.floor(value) for value in (x, y, z))
        if not self.is_replaceable(*position):
            raise ValueError(f"target position {position} is not replaceable")

        item = next(
            (
                inventory_item
                for inventory_item in self._inventory_items()
                if str(inventory_item.name) == block_type
            ),
            None,
        )
        if item is None:
            raise RuntimeError(f"no {block_type} available in inventory")

        self.move_within_reach(*position)
        reference = None
        face = None
        for offset in (
            (0, -1, 0),
            (0, 1, 0),
            (-1, 0, 0),
            (1, 0, 0),
            (0, 0, -1),
            (0, 0, 1),
        ):
            candidate = tuple(
                coordinate + delta for coordinate, delta in zip(position, offset)
            )
            candidate_block = self._block_at(*candidate)
            if candidate_block is None or str(candidate_block.boundingBox) != "block":
                continue
            reference = candidate_block
            face = tuple(-delta for delta in offset)
            break

        if reference is None:
            raise RuntimeError(f"no solid neighbor available to place block at {position}")

        self._client().equip(item, "hand")
        self.look_at_block(*position)
        self._client().placeBlock(reference, self._vec3(*face))
        self.wait_for_block_placement(position, block_type, timeout=timeout)
        return {
            "block_type": block_type,
            "position": self._position_dict(position),
        }

    def start_breaking_block(self, x, y, z):
        """Start digging a block in a background thread."""
        if self._dig_thread is not None and self._dig_thread.is_alive():
            raise RuntimeError("already breaking a block")
        if not self.is_block_reachable(x, y, z):
            raise RuntimeError(f"block at {(x, y, z)} is not within digging reach")
        block = self._block_at(x, y, z)
        if block is None or not bool(block.diggable):
            raise ValueError(f"block at {(x, y, z)} is not breakable")

        self.look_at_block(x, y, z)
        self._dig_error = None
        self._digging_position = (x, y, z)

        def dig():
            try:
                self._client().dig(block, True, "raycast")
            except Exception as error:
                self._dig_error = error

        self._dig_thread = threading.Thread(target=dig, daemon=True)
        self._dig_thread.start()
        return self._dig_thread

    def finish_breaking_block(self, timeout=30.0):
        """Wait for the active block break to finish."""
        if self._dig_thread is None:
            return True
        self._dig_thread.join(timeout)
        if self._dig_thread.is_alive():
            raise TimeoutError("block breaking did not finish before timeout")
        error = self._dig_error
        self._dig_thread = None
        self._digging_position = None
        self._dig_error = None
        if error is not None:
            raise error
        return True

    def cancel_breaking(self):
        """Cancel an active block break."""
        if self.bot is None:
            return False
        try:
            self._client().stopDigging()
        finally:
            self._dig_thread = None
            self._dig_error = None
            self._digging_position = None
        return True

    def mine_block(self, x, y, z):
        """Break one block and wait for completion."""
        self.start_breaking_block(x, y, z)
        return self.finish_breaking_block()

    def _connected_logs(self, start, log_type, max_logs):
        pending = [tuple(start)]
        visited = set()
        logs = []
        while pending and len(logs) <= max_logs:
            position = pending.pop()
            if position in visited:
                continue
            visited.add(position)
            block = self._block_at(*position)
            if block is None or str(block.name) != log_type:
                continue
            logs.append(position)
            x, y, z = position
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        if (dx, dy, dz) != (0, 0, 0):
                            pending.append((x + dx, y + dy, z + dz))
        return logs

    def _jump_place_underfoot(self, block_type, timeout=3.0):
        x, y, z = self._block_position()
        target = (x, y, z)
        reference_position = (x, y - 1, z)
        reference = self._block_at(*reference_position)
        if reference is None or str(reference.boundingBox) != "block":
            raise RuntimeError("cannot pillar without a solid block under the bot")
        item = next(
            (
                inventory_item
                for inventory_item in self._inventory_items()
                if str(inventory_item.name) == block_type
            ),
            None,
        )
        if item is None:
            raise RuntimeError(f"no harvested {block_type} available for scaffolding")

        bot = self._require_bot()
        movements = bot.movements
        scaffold_ids = self._proxy_values(movements.scafoldingBlocks)
        if int(item.type) not in {int(item_id) for item_id in scaffold_ids}:
            try:
                movements.scafoldingBlocks.push(int(item.type))
            except AttributeError:
                movements.scafoldingBlocks.append(int(item.type))
        previous_towers = bool(movements.allow1by1towers)
        movements.allow1by1towers = True
        bot.pathfinder.setMovements(movements)
        try:
            self.move_to(x, y + 1, z)
        finally:
            movements.allow1by1towers = previous_towers
            bot.pathfinder.setMovements(movements)

        self.wait_for_block_placement(target, block_type, timeout=timeout)
        if self._block_position() != (x, y + 1, z):
            raise RuntimeError("pathfinder did not finish on top of placed scaffolding")
        return {"type": block_type, "position": target}

    def _remove_scaffold(self, scaffold, timeout=3.0):
        position = tuple(scaffold["position"])
        block_type = scaffold["type"]
        x, y, z = position
        bot_x, bot_y, bot_z = self._block_position()
        if (bot_x, bot_z) != (x, z) or bot_y != y + 1:
            raise RuntimeError(f"bot is not standing above scaffold at {position}")
        block = self._block_at(*position)
        if block is None or str(block.name) != block_type:
            raise RuntimeError(f"tracked scaffold at {position} was replaced or removed")
        landing = self._block_at(x, y - 1, z)
        if (
            landing is None
            or str(landing.boundingBox) != "block"
            or str(landing.name) in HAZARDOUS_BLOCKS
        ):
            raise RuntimeError(f"no safe landing beneath scaffold at {position}")

        original_type = int(block.type)
        self.sneak(True)
        try:
            self.mine_block(*position)
            self.wait_for_block_change(position, original_type)
            deadline = time.monotonic() + timeout
            while self.get_position()[1] > y + 0.1:
                if time.monotonic() >= deadline:
                    raise TimeoutError("bot did not descend after removing scaffolding")
                time.sleep(0.05)
        finally:
            self.sneak(False)
        return position

    def mine_nearest_tree(self, radius=64, max_logs=64, timeout=5.0):
        """Mine one connected tree, using harvested logs as temporary scaffolding."""
        if radius <= 0 or max_logs <= 0 or timeout <= 0:
            raise ValueError("radius, max_logs, and timeout must be positive")
        trees = self.find_nearest_blocks(LOG_BLOCKS, radius, max_results=1)
        if not trees:
            raise RuntimeError(f"no loaded tree found within {radius} blocks")
        nearest = trees[0]
        log_type = nearest["type"]
        start = tuple(nearest["position"])
        logs = self._connected_logs(start, log_type, max_logs)
        if len(logs) > max_logs:
            raise RuntimeError(
                f"connected tree exceeds max_logs={max_logs}; refusing a partial snapshot"
            )

        self.move_within_reach(*start)
        inventory_before = self.get_inventory_counts()
        remaining = set(logs)
        mined = []
        scaffolds = []
        cleanup_error = None
        try:
            while remaining:
                reachable = sorted(
                    (position for position in remaining if self.is_block_reachable(*position)),
                    key=lambda position: (position[1], self._distance(self.get_position(), position)),
                )
                if reachable:
                    for position in reachable:
                        block = self._block_at(*position)
                        remaining.discard(position)
                        if block is None or str(block.name) != log_type:
                            continue
                        self.equip_best_tool_for_block(log_type)
                        original_type = int(block.type)
                        self.mine_block(*position)
                        self.wait_for_block_change(position, original_type)
                        mined.append(position)
                    if not scaffolds:
                        self.collect_dropped_items(timeout=timeout, radius=8)
                    continue

                if not scaffolds:
                    base = min(logs, key=lambda position: position[1])
                    self.move_near(*base, distance=0.4)
                    if any(self.is_block_reachable(*position) for position in remaining):
                        continue
                scaffolds.append(self._jump_place_underfoot(log_type, timeout=timeout))
        finally:
            for scaffold in reversed(scaffolds):
                try:
                    self._remove_scaffold(scaffold, timeout=timeout)
                except Exception as error:
                    cleanup_error = error
                    break

        self.collect_dropped_items(timeout=timeout, radius=8)
        inventory_after = self.get_inventory_counts()
        temporary_remaining = [
            scaffold
            for scaffold in scaffolds
            if (
                (block := self._block_at(*scaffold["position"])) is not None
                and str(block.name) == scaffold["type"]
            )
        ]
        completed = not remaining and not temporary_remaining and cleanup_error is None
        return {
            "completed": completed,
            "log_type": log_type,
            "tree_origin": self._position_dict(start),
            "logs_found": len(logs),
            "logs_mined": len(mined),
            "logs_remaining": [self._position_dict(position) for position in sorted(remaining)],
            "temporary_blocks_remaining": temporary_remaining,
            "inventory_gained": {
                item_name: count - inventory_before.get(item_name, 0)
                for item_name, count in inventory_after.items()
                if count > inventory_before.get(item_name, 0)
            },
            "cleanup_error": str(cleanup_error) if cleanup_error else None,
        }

    def _entity_ids(self):
        try:
            return list(self.bot.entities.keys())
        except Exception:
            try:
                return list(self.bot.entities)
            except Exception:
                return []

    @staticmethod
    def _is_dropped_item(entity):
        return (
            str(getattr(entity, "name", "")) in {"item", "Item", "item_stack"}
            or str(getattr(entity, "displayName", "")) == "Item"
        )

    def get_dropped_items(self, radius=8):
        """Return loaded dropped-item entities near the bot."""
        origin = self.get_position()
        dropped = []
        entity_ids = set(self._entity_ids()) | self._recent_drop_ids
        for entity_id in entity_ids:
            try:
                entity = self.bot.entities[entity_id]
                if entity_id not in self._recent_drop_ids and not self._is_dropped_item(entity):
                    continue
                distance = self._distance(origin, entity.position)
                if distance <= radius:
                    dropped.append(
                        {
                            "id": int(entity_id),
                            "distance": distance,
                            "position": self._position_dict(entity.position),
                        }
                    )
            except Exception:
                continue
        dropped.sort(key=lambda item: item["distance"])
        return dropped

    def collect_dropped_items(
        self, timeout=5.0, radius=8, ignore_entity_ids=None, stop_when=None
    ):
        """Walk over newly dropped item entities and return their entity IDs."""
        ignored = set(ignore_entity_ids or ())
        collected = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if stop_when is not None and stop_when():
                break
            targets = [
                item for item in self.get_dropped_items(radius) if item["id"] not in ignored
            ]
            if not targets:
                time.sleep(0.1)
                continue

            target = targets[0]
            entity_id = target["id"]
            self.move_to_entity(entity_id, distance=0)
            collected.append(entity_id)
            ignored.add(entity_id)
            time.sleep(0.1)
        return collected

    def wait_for_inventory_change(self, previous_counts, timeout=5.0):
        """Wait for at least one inventory item count to increase."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.get_inventory_counts()
            if any(
                count > previous_counts.get(item_name, 0)
                for item_name, count in current.items()
            ):
                return current
            time.sleep(0.1)
        raise TimeoutError("inventory did not increase after mining")

    def wait_for_block_change(self, position, original_type, timeout=3.0):
        """Require a block change to remain stable after the dig prediction."""
        deadline = time.monotonic() + timeout
        changed_since = None
        while time.monotonic() < deadline:
            block = self._block_at(*position)
            changed = block is None or int(block.type) != int(original_type)
            if changed:
                if changed_since is None:
                    changed_since = time.monotonic()
                elif time.monotonic() - changed_since >= 0.5:
                    return block
            else:
                changed_since = None
            time.sleep(0.05)
        raise RuntimeError(
            f"block at {position} was not broken; check visibility, server "
            "permissions, and spawn protection"
        )

    def wait_for_block_placement(self, position, block_type, timeout=3.0):
        """Require the expected placed block to remain visible briefly."""
        deadline = time.monotonic() + timeout
        placed_since = None
        while time.monotonic() < deadline:
            block = self._block_at(*position)
            if block is not None and str(block.name) == block_type:
                if placed_since is None:
                    placed_since = time.monotonic()
                elif time.monotonic() - placed_since >= 0.2:
                    return block
            else:
                placed_since = None
            time.sleep(0.05)
        raise RuntimeError(f"{block_type} was not placed at {position}")

    def find_nearest_collectible_block(self, block_type, radius):
        """Find a target that is not supporting the bot's current column."""
        bot_x, _, bot_z = self._block_position()
        for position in self.find_blocks(block_type, radius):
            x, _, z = position
            if (math.floor(x), math.floor(z)) != (bot_x, bot_z):
                return position
        return None

    def collect_resource(self, block_type, quantity, radius=32, timeout=5.0):
        """Mine, pick up, and verify a quantity of blocks as one workflow."""
        if quantity < 0:
            raise ValueError("quantity must be non-negative")

        logger.info(
            "collecting %d %s within radius %d", quantity, block_type, radius
        )
        inventory_before = self.get_inventory_counts()
        mined = 0
        mined_positions = []
        for _ in range(quantity):
            position = self.find_nearest_collectible_block(block_type, radius)
            if position is None:
                break

            self.move_within_reach(*position)
            equipped = self.equip_best_tool_for_block(block_type)
            if self.get_required_tool(block_type) is not None and equipped is None:
                raise RuntimeError(f"no suitable tool available to harvest {block_type}")

            counts_before_mining = self.get_inventory_counts()
            existing_drops = {item["id"] for item in self.get_dropped_items(radius=8)}
            block = self._block_at(*position)
            if block is None:
                continue
            original_block_type = int(block.type)
            self.mine_block(*position)
            self.wait_for_block_change(position, original_block_type)
            self.move_near(
                position[0] + 0.5,
                position[1] + 1,
                position[2] + 0.5,
                distance=1.25,
            )
            self.collect_dropped_items(
                timeout=timeout,
                radius=8,
                ignore_entity_ids=existing_drops,
                stop_when=lambda: any(
                    count > counts_before_mining.get(item_name, 0)
                    for item_name, count in self.get_inventory_counts().items()
                ),
            )
            try:
                self.wait_for_inventory_change(counts_before_mining, timeout=timeout)
            except TimeoutError as error:
                raise TimeoutError(
                    f"inventory did not increase after mining {block_type} at "
                    f"{position}; game mode is {self.get_game_mode()}"
                ) from error
            mined += 1
            mined_positions.append(self._position_dict(position))

        inventory_after = self.get_inventory_counts()
        gained = {
            item_name: count - inventory_before.get(item_name, 0)
            for item_name, count in inventory_after.items()
            if count > inventory_before.get(item_name, 0)
        }
        logger.info("collected %d %s: gained=%s", mined, block_type, gained)
        return {
            "block_type": block_type,
            "requested": quantity,
            "mined": mined,
            "mined_positions": mined_positions,
            "gained": gained,
            "inventory_before": inventory_before,
            "inventory_after": inventory_after,
        }

    def mine_nearest(self, block_type, radius=32):
        position = self.find_nearest_block(block_type, radius)
        if position is None:
            return False
        return self.mine_block(*position)

    def mine_blocks(self, block_type, quantity):
        if quantity < 0:
            raise ValueError("quantity must be non-negative")
        mined = 0
        while mined < quantity:
            if not self.mine_nearest(block_type):
                break
            mined += 1
        return mined

    def clear_blocks(self, positions):
        cleared = 0
        for position in positions:
            x, y, z = self._position_tuple(position)
            if self.is_air(x, y, z):
                continue
            self.mine_block(x, y, z)
            cleared += 1
        return cleared

    def dig_tunnel(self, direction, length):
        """Dig and traverse a two-block-high horizontal tunnel."""
        if length < 0:
            raise ValueError("length must be non-negative")

        yaw, _ = self.get_rotation()
        forward = (round(-math.sin(yaw)), round(math.cos(yaw)))
        offsets = {
            "forward": forward,
            "back": (-forward[0], -forward[1]),
            "left": (forward[1], -forward[0]),
            "right": (-forward[1], forward[0]),
            "north": (0, -1),
            "south": (0, 1),
            "east": (1, 0),
            "west": (-1, 0),
        }
        if direction not in offsets:
            raise ValueError(f"unsupported tunnel direction: {direction}")

        dx, dz = offsets[direction]
        completed = 0
        for _ in range(length):
            x, y, z = self._block_position()
            target = (x + dx, y, z + dz)
            for block_y in (target[1], target[1] + 1):
                if not self.is_air(target[0], block_y, target[2]):
                    self.mine_block(target[0], block_y, target[2])
            self.move_near(target[0], target[1], target[2], distance=0.4)
            completed += 1
        return completed

    def send_chat(self, message):
        """Send a chat message to the Minecraft server."""
        if not isinstance(message, str) or not message:
            raise ValueError("message must be a non-empty string")
        self._require_bot().chat(message)
        return True
