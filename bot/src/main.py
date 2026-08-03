import heapq
import math
import random
import threading
import time

import lodestone


AIR_BLOCKS = {"air", "cave_air", "void_air"}
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


class Bot:
    """Agent-friendly wrapper around a Lodestone/Mineflayer bot."""

    def __init__(self, host, auth, port, username, version):
        self.host = host
        self.auth = auth
        self.port = port
        self.username = username
        self.version = version
        self.bot = None
        self._spawned = threading.Event()
        self._vec3_factory = None
        self._last_goal_position = None
        self._dig_thread = None
        self._dig_error = None
        self._digging_position = None

    # Lifecycle

    def connect(self, host=None, port=None, username=None):
        """Connect to a server, optionally replacing stored connection values."""
        if self.is_connected():
            raise RuntimeError("bot is already connected")

        if host is not None:
            self.host = host
        if port is not None:
            self.port = port
        if username is not None:
            self.username = username

        self._spawned.clear()
        self.bot = lodestone.createBot(
            host=self.host,
            auth=self.auth,
            port=self.port,
            username=self.username,
            version=self.version,
        )

        @self.bot.on("chat")
        def on_chat(_, username, message, *args):
            self.on_chat(username, message)

        @self.bot.on("spawn")
        def on_spawn(*_):
            self._spawned.set()

        if self._entity_or_none() is not None:
            self._spawned.set()

        self.bot.chat("Bot successfully loaded. Say 'walk' or 'walk 3' to move me.")
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
            self._client().end()
        finally:
            self.bot = None
            self._spawned.clear()
            self._last_goal_position = None
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

    # Basic movement

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

    # Pathfinder navigation

    def _navigate(self, goal):
        self._require_bot().pathfinder.goto(goal, timeout=600_000_000)

    def move_to(self, x, y, z):
        """Navigate to an exact position."""
        self._last_goal_position = (x, y, z)
        goal = self._require_bot().goals.GoalBlock(x, y, z)
        self._navigate(goal)

    def move_near(self, x, y, z, distance=1.5):
        """Navigate to within distance of a position."""
        self._last_goal_position = (x, y, z)
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
        self._require_bot().pathfinder.stop()

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
                    if candidate in costs or not self.can_move_between(current, candidate):
                        continue
                    next_cost = current_cost + (1.5 if dy else 1.0)
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

    # State and orientation

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

    # World observation

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
        try:
            entity_ids = list(self.bot.entities.keys())
        except Exception:
            try:
                entity_ids = list(self.bot.entities)
            except Exception:
                entity_ids = []

        for entity_id in entity_ids:
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
        block = self._require_bot().registry.blocksByName[block_type]
        if not block:
            raise KeyError(f"unknown block type: {block_type}")
        return block

    def find_blocks(self, block_type, radius, count=64):
        """Find matching loaded blocks ordered by Mineflayer distance."""
        if radius < 0:
            raise ValueError("radius must be non-negative")
        block = self._block_type(block_type)
        positions = self._client().findBlocks(
            {"matching": block.id, "maxDistance": radius, "count": count}
        )
        try:
            length = len(positions.valueOf())
        except Exception:
            length = len(positions)
        return [self._position_tuple(positions[index]) for index in range(length)]

    def find_nearest_block(self, block_type, radius):
        blocks = self.find_blocks(block_type, radius, count=1)
        return blocks[0] if blocks else None

    def find_nearest_blocks(self, block_types, radius):
        origin = self.get_position()
        blocks = []
        for block_type in block_types:
            for position in self.find_blocks(block_type, radius):
                blocks.append({"type": block_type, "position": position})
        blocks.sort(key=lambda result: self._distance(origin, result["position"]))
        return blocks

    def get_loaded_chunks(self):
        columns = self._require_bot().world.getColumns()
        try:
            length = len(columns.valueOf())
        except Exception:
            length = len(columns)
        return [
            {
                "x": int(columns[index].chunkX),
                "z": int(columns[index].chunkZ),
            }
            for index in range(length)
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

    # Block predicates and tools

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

    # Digging and mining

    def start_breaking_block(self, x, y, z):
        """Start digging a block in a background thread."""
        if self._dig_thread is not None and self._dig_thread.is_alive():
            raise RuntimeError("already breaking a block")
        block = self._block_at(x, y, z)
        if block is None or not bool(block.diggable):
            raise ValueError(f"block at {(x, y, z)} is not breakable")

        self.look_at_block(x, y, z)
        self._dig_error = None
        self._digging_position = (x, y, z)

        def dig():
            try:
                self._client().dig(block)
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

    def on_chat(self, username, message):
        if username == self._require_bot().username:
            return
        self.bot.chat("Hello")


if __name__ == "__main__":
    bot = Bot(
        host="localhost",
        auth="offline",
        port=25565,
        username="BotName",
        version="1.21.11",
    )
    bot.connect()
    bot.wait_until_spawned()
    bot.walk_forward()
