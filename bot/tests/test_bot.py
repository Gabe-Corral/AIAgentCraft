import math
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from src.bot import (
    Bot,
    NavigationCancelledError,
    NavigationStuckError,
    NavigationTimeoutError,
    UnknownBlockTypeError,
)


class BotMovementTests(unittest.TestCase):
    def setUp(self):
        self.wrapper = Bot.__new__(Bot)
        self.wrapper.bot = MagicMock()

    @patch("src.bot.time.sleep")
    def test_timed_movement_controls(self, sleep):
        movements = (
            ("walk_forward", "forward"),
            ("walk_backward", "back"),
            ("strafe_left", "left"),
            ("strafe_right", "right"),
        )

        for method_name, control in movements:
            with self.subTest(method=method_name):
                self.wrapper.bot.set_control_state.reset_mock()
                sleep.reset_mock()

                getattr(self.wrapper, method_name)(2.5)

                self.wrapper.bot.set_control_state.assert_has_calls(
                    [call(control, True), call(control, False)]
                )
                sleep.assert_called_once_with(2.5)

    @patch("src.bot.time.sleep", side_effect=RuntimeError("interrupted"))
    def test_timed_movement_releases_control_when_interrupted(self, _sleep):
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.wrapper.walk_forward(1)

        self.wrapper.bot.set_control_state.assert_has_calls(
            [call("forward", True), call("forward", False)]
        )

    @patch("src.bot.time.sleep")
    def test_jump_taps_jump_control(self, sleep):
        self.wrapper.jump()

        self.wrapper.bot.set_control_state.assert_has_calls(
            [call("jump", True), call("jump", False)]
        )
        sleep.assert_called_once_with(0.1)

    def test_sprint_and_sneak_toggle_controls(self):
        self.wrapper.sprint()
        self.wrapper.sprint(False)
        self.wrapper.sneak()
        self.wrapper.sneak(False)

        self.wrapper.bot.set_control_state.assert_has_calls(
            [
                call("sprint", True),
                call("sprint", False),
                call("sneak", True),
                call("sneak", False),
            ]
        )

    def test_stop_movement_clears_control_states(self):
        self.wrapper.stop_movement()

        self.wrapper.bot.clear_control_states.assert_called_once_with()


class BotNavigationTests(unittest.TestCase):
    def setUp(self):
        self.wrapper = Bot(
            "localhost",
            "offline",
            25565,
            "Agent",
            "1.21.11",
            navigation_timeout=0.2,
            navigation_stuck_timeout=0.05,
            navigation_poll_interval=0.005,
            navigation_min_progress=0.01,
        )
        self.wrapper.bot = MagicMock()
        self.wrapper.bot.entity = SimpleNamespace(
            position=SimpleNamespace(x=0, y=64, z=0)
        )

    def assert_navigates_to(self, goal):
        self.wrapper.bot.pathfinder.goto.assert_called_once_with(
            goal, timeout=1.2
        )

    def test_move_to_uses_exact_block_goal(self):
        goal = self.wrapper.bot.goals.GoalBlock.return_value

        self.wrapper.move_to(1, 2, 3)

        self.wrapper.bot.goals.GoalBlock.assert_called_once_with(1, 2, 3)
        self.assert_navigates_to(goal)

    def test_move_near_uses_requested_distance(self):
        goal = self.wrapper.bot.goals.GoalNear.return_value

        self.wrapper.move_near(1, 2, 3, distance=4.5)

        self.wrapper.bot.goals.GoalNear.assert_called_once_with(1, 2, 3, 4.5)
        self.assert_navigates_to(goal)

    def test_move_near_uses_block_aware_navigation_for_solid_target(self):
        with (
            patch.object(self.wrapper, "is_solid", return_value=True),
            patch.object(
                self.wrapper, "move_within_reach", return_value=True
            ) as move_within_reach,
        ):
            result = self.wrapper.move_near(1, 2, 3, distance=4.5)

        self.assertTrue(result)
        move_within_reach.assert_called_once_with(1, 2, 3)
        self.wrapper.bot.goals.GoalNear.assert_not_called()

    def test_move_to_block_uses_adjacent_block_goal(self):
        goal = self.wrapper.bot.goals.GoalGetToBlock.return_value

        self.wrapper.move_to_block(1, 2, 3)

        self.wrapper.bot.goals.GoalGetToBlock.assert_called_once_with(1, 2, 3)
        self.assert_navigates_to(goal)

    def test_move_to_entity_uses_follow_goal_once(self):
        entity = object()
        self.wrapper.bot.entities = {42: entity}
        goal = self.wrapper.bot.goals.GoalFollow.return_value

        self.wrapper.move_to_entity(42, distance=2.5)

        self.wrapper.bot.goals.GoalFollow.assert_called_once_with(entity, 2.5)
        self.assert_navigates_to(goal)

    def test_follow_entity_sets_dynamic_goal(self):
        entity = object()
        self.wrapper.bot.entities = {42: entity}
        goal = self.wrapper.bot.goals.GoalFollow.return_value

        self.wrapper.follow_entity(42, distance=3.5)

        self.wrapper.bot.goals.GoalFollow.assert_called_once_with(entity, 3.5)
        self.wrapper.bot.pathfinder.setGoal.assert_called_once_with(goal, True)

    def test_missing_entity_is_rejected(self):
        self.wrapper.bot.entities = {42: None}

        with self.assertRaisesRegex(ValueError, "Entity 42 is not loaded"):
            self.wrapper.move_to_entity(42)

    def test_move_away_uses_inverted_near_goal(self):
        near_goal = self.wrapper.bot.goals.GoalNear.return_value
        inverted_goal = self.wrapper.bot.goals.GoalInvert.return_value

        self.wrapper.move_away_from(1, 2, 3, distance=5)

        self.wrapper.bot.goals.GoalNear.assert_called_once_with(1, 2, 3, 5)
        self.wrapper.bot.goals.GoalInvert.assert_called_once_with(near_goal)
        self.assert_navigates_to(inverted_goal)

    @patch("src.bot.random.random", return_value=1)
    @patch("src.bot.random.uniform", return_value=0)
    def test_wander_picks_destination_within_radius(self, uniform, random_value):
        self.wrapper.bot.entity.position = SimpleNamespace(x=10, y=20, z=30)

        with patch.object(self.wrapper, "move_near") as move_near:
            self.wrapper.wander(4)

        uniform.assert_called_once_with(0, 2 * math.pi)
        random_value.assert_called_once_with()
        move_near.assert_called_once_with(14, 20, 30)

    def test_wander_rejects_negative_radius(self):
        with self.assertRaisesRegex(ValueError, "radius must be non-negative"):
            self.wrapper.wander(-1)

    def test_stop_navigation_clears_goal_immediately(self):
        self.wrapper.stop_navigation()

        self.wrapper.bot.pathfinder.setGoal.assert_called_once_with(None)
        self.wrapper.bot.pathfinder.stop.assert_not_called()

    def test_navigation_allows_continuing_progress(self):
        self.wrapper.navigation_stuck_timeout = 0.02
        position = 0

        def get_position():
            nonlocal position
            position += 0.02
            return (position, 64, 0)

        self.wrapper.bot.pathfinder.goto.side_effect = lambda *_args, **_kwargs: time.sleep(
            0.04
        )
        with patch.object(self.wrapper, "get_position", side_effect=get_position):
            self.wrapper._navigate(object())

        self.wrapper.bot.pathfinder.stop.assert_not_called()
        self.wrapper.bot.pathfinder.setGoal.assert_not_called()
        self.wrapper.bot.clear_control_states.assert_not_called()

    def test_navigation_detects_stuck_state_and_cleans_up(self):
        stopped = threading.Event()
        self.wrapper.navigation_stuck_timeout = 0.02
        self.wrapper.bot.pathfinder.goto.side_effect = (
            lambda *_args, **_kwargs: stopped.wait(1)
        )
        self.wrapper.bot.pathfinder.setGoal.side_effect = lambda _goal: stopped.set()

        with self.assertRaisesRegex(NavigationStuckError, "no position progress"):
            self.wrapper._navigate(object())

        self.wrapper.bot.pathfinder.setGoal.assert_called_once_with(None)
        self.wrapper.bot.clear_control_states.assert_called_once_with()

    def test_navigation_enforces_timeout_and_cleans_up(self):
        stopped = threading.Event()
        self.wrapper.navigation_timeout = 0.02
        self.wrapper.navigation_stuck_timeout = 1
        self.wrapper.bot.pathfinder.goto.side_effect = (
            lambda *_args, **_kwargs: stopped.wait(1)
        )
        self.wrapper.bot.pathfinder.setGoal.side_effect = lambda _goal: stopped.set()

        with self.assertRaisesRegex(NavigationTimeoutError, "timed out after 0.02"):
            self.wrapper._navigate(object())

        self.wrapper.bot.pathfinder.setGoal.assert_called_once_with(None)
        self.wrapper.bot.clear_control_states.assert_called_once_with()

    def test_navigation_can_be_cancelled(self):
        stopped = threading.Event()
        failure = []
        self.wrapper.bot.pathfinder.goto.side_effect = (
            lambda *_args, **_kwargs: stopped.wait(1)
        )
        self.wrapper.bot.pathfinder.setGoal.side_effect = lambda _goal: stopped.set()

        def navigate():
            try:
                self.wrapper._navigate(object())
            except BaseException as error:
                failure.append(error)

        worker = threading.Thread(target=navigate)
        worker.start()
        time.sleep(0.01)
        self.wrapper.stop_navigation()
        worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertIsInstance(failure[0], NavigationCancelledError)
        self.wrapper.bot.clear_control_states.assert_called_once_with()

    def test_cleanup_does_not_cancel_subsequent_navigation(self):
        for trigger in ("failure", "idle_stop"):
            with self.subTest(trigger=trigger):
                pending_stop = False

                def stop():
                    nonlocal pending_stop
                    pending_stop = True

                def set_goal(_goal):
                    nonlocal pending_stop
                    pending_stop = False

                def goto(goal, **_kwargs):
                    was_stopped = pending_stop
                    set_goal(goal)
                    if was_stopped:
                        raise RuntimeError("PathStopped")

                pathfinder = self.wrapper.bot.pathfinder
                pathfinder.stop.side_effect = stop
                pathfinder.setGoal.side_effect = set_goal
                pathfinder.goto.side_effect = goto
                if trigger == "failure":
                    stop()
                    with self.assertRaisesRegex(RuntimeError, "PathStopped"):
                        self.wrapper._navigate(object())
                else:
                    self.wrapper.stop_navigation()

                self.wrapper._navigate(object())
                self.wrapper._cleanup_navigation()
                self.wrapper._cleanup_navigation()
                self.wrapper._navigate(object())


class BotLifecycleTests(unittest.TestCase):
    def test_init_stores_configuration_without_connecting(self):
        wrapper = Bot("example.test", "offline", 25565, "Agent", "1.21.11")

        self.assertEqual(wrapper.host, "example.test")
        self.assertEqual(wrapper.username, "Agent")
        self.assertFalse(wrapper.is_connected())

    def test_send_chat_uses_connected_bot(self):
        wrapper = Bot("localhost", "offline", 25565, "Agent", "1.21.11")
        wrapper.bot = MagicMock()

        self.assertTrue(wrapper.send_chat("hello"))

        wrapper.bot.chat.assert_called_once_with("hello")

    @patch("src.bot.lodestone.createBot")
    def test_connect_and_disconnect(self, create_bot):
        lodestone_bot = MagicMock()
        lodestone_bot.entity = None

        def register(_event):
            return lambda callback: callback

        lodestone_bot.on.side_effect = register
        create_bot.return_value = lodestone_bot
        wrapper = Bot("example.test", "offline", 25565, "Agent", "1.21.11")

        wrapper.connect()

        create_bot.assert_called_once_with(
            host="example.test",
            auth="offline",
            port=25565,
            username="Agent",
            version="1.21.11",
            ls_disable_viewer=True,
        )
        self.assertTrue(wrapper.is_connected())
        self.assertFalse(lodestone_bot.movements.canDig)

        self.assertTrue(wrapper.disconnect())
        lodestone_bot.stop.assert_called_once_with()
        self.assertFalse(wrapper.is_connected())


class BotStateTests(unittest.TestCase):
    def setUp(self):
        self.wrapper = Bot.__new__(Bot)
        self.wrapper.bot = MagicMock()
        self.wrapper._spawned = MagicMock()
        self.wrapper._digging_position = None
        self.wrapper.bot.entity = SimpleNamespace(
            position=SimpleNamespace(x=1.5, y=64, z=-3.25),
            yaw=0.5,
            pitch=-0.25,
        )

    def test_position_and_rotation(self):
        self.assertEqual(self.wrapper.get_position(), (1.5, 64, -3.25))
        self.assertEqual(self.wrapper.get_rotation(), (0.5, -0.25))

    def test_turning_converts_degrees_to_radians(self):
        with patch.object(self.wrapper, "set_yaw") as set_yaw:
            self.wrapper.turn_right(90)

        set_yaw.assert_called_once_with(0.5 + math.pi / 2)

    def test_path_cost_penalizes_vertical_travel(self):
        self.assertEqual(
            self.wrapper.estimate_path_cost((0, 0, 0), (3, 2, 4)),
            10,
        )

    def test_find_path_returns_walkable_route(self):
        with patch.object(self.wrapper, "can_move_between", return_value=True):
            path = self.wrapper.find_path((0, 0, 0), (2, 0, 0), max_nodes=20)

        self.assertEqual(path[0], (0, 0, 0))
        self.assertEqual(path[-1], (2, 0, 0))

    def test_find_path_replaces_a_more_expensive_discovered_route(self):
        start = (0, 0, 0)
        expensive_parent = (1, 1, 0)
        cheap_parent = (0, 0, 1)
        goal = (1, 0, 1)
        allowed = {
            (start, expensive_parent),
            (start, cheap_parent),
            (expensive_parent, goal),
            (cheap_parent, goal),
        }
        estimates = {
            start: 0,
            expensive_parent: 0,
            cheap_parent: 1,
            goal: 10,
        }

        with (
            patch.object(
                self.wrapper,
                "can_move_between",
                side_effect=lambda current, candidate: (current, candidate) in allowed,
            ),
            patch.object(
                self.wrapper,
                "estimate_path_cost",
                side_effect=lambda current, _goal: estimates.get(current, 100),
            ),
        ):
            path = self.wrapper.find_path(start, goal, max_nodes=20)

        self.assertEqual(path, [start, cheap_parent, goal])


class BotBlockTests(unittest.TestCase):
    def setUp(self):
        self.wrapper = Bot.__new__(Bot)

    def test_block_predicates(self):
        air = SimpleNamespace(name="air", boundingBox="empty", diggable=False)
        stone = SimpleNamespace(name="stone", boundingBox="block", diggable=True)
        lava = SimpleNamespace(name="lava", boundingBox="empty", diggable=False)

        with patch.object(self.wrapper, "_block_at", return_value=air):
            self.assertTrue(self.wrapper.is_air(0, 0, 0))
            self.assertTrue(self.wrapper.is_replaceable(0, 0, 0))

        with patch.object(self.wrapper, "_block_at", return_value=stone):
            self.assertTrue(self.wrapper.is_solid(0, 0, 0))
            self.assertTrue(self.wrapper.is_breakable(0, 0, 0))

        with patch.object(self.wrapper, "_block_at", return_value=lava):
            self.assertTrue(self.wrapper.is_liquid(0, 0, 0))
            self.assertTrue(self.wrapper.is_hazardous(0, 0, 0))

    def test_unknown_block_type_suggests_canonical_name(self):
        self.wrapper.bot = SimpleNamespace(
            registry=SimpleNamespace(
                blocksByName={"oak_log": SimpleNamespace(id=1), "oak_leaves": SimpleNamespace(id=2)}
            )
        )

        with self.assertRaisesRegex(UnknownBlockTypeError, "did you mean: oak_log"):
            self.wrapper._block_type("oak_logs")

    def test_plural_block_search_validates_all_names_before_searching(self):
        self.wrapper.bot = SimpleNamespace(
            registry=SimpleNamespace(
                blocksByName={"oak_log": SimpleNamespace(id=1)}
            )
        )
        with (
            patch.object(self.wrapper, "get_position", return_value=(0, 0, 0)),
            patch.object(self.wrapper, "find_blocks") as find_blocks,
        ):
            with self.assertRaises(UnknownBlockTypeError):
                self.wrapper.find_nearest_blocks(["oak_log", "log"], 16)

        find_blocks.assert_not_called()

    def test_plural_block_search_limits_results(self):
        self.wrapper.bot = SimpleNamespace(
            registry=SimpleNamespace(
                blocksByName={
                    "oak_log": SimpleNamespace(id=1),
                    "birch_log": SimpleNamespace(id=2),
                }
            )
        )
        with (
            patch.object(self.wrapper, "get_position", return_value=(0, 0, 0)),
            patch.object(
                self.wrapper,
                "find_blocks",
                side_effect=[[(5, 0, 0), (2, 0, 0)], [(1, 0, 0)]],
            ) as find_blocks,
        ):
            results = self.wrapper.find_nearest_blocks(
                ["oak_log", "birch_log"], 50, max_results=2
            )

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["position"], (1, 0, 0))
        self.assertEqual(find_blocks.call_args_list[0].kwargs, {"count": 2})

    def test_mine_block_waits_for_dig(self):
        block = SimpleNamespace(name="stone", diggable=True)
        client = MagicMock()
        self.wrapper._dig_thread = None
        self.wrapper._dig_error = None
        self.wrapper._digging_position = None
        with (
            patch.object(self.wrapper, "is_block_reachable", return_value=True),
            patch.object(self.wrapper, "_block_at", side_effect=[block, None]),
            patch.object(self.wrapper, "look_at_block"),
            patch.object(self.wrapper, "_client", return_value=client),
        ):
            self.assertTrue(self.wrapper.mine_block(1, 2, 3))

        client.dig.assert_called_once_with(block, True, "raycast")
        self.assertIsNone(self.wrapper._digging_position)

    def test_mine_block_rejects_out_of_reach_target(self):
        self.wrapper._dig_thread = None
        with patch.object(self.wrapper, "is_block_reachable", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "not within digging reach"):
                self.wrapper.mine_block(6, 16, -104)

    def test_connected_logs_detects_diagonal_branches(self):
        positions = {
            (0, 64, 0): SimpleNamespace(name="oak_log"),
            (0, 65, 0): SimpleNamespace(name="oak_log"),
            (1, 66, 1): SimpleNamespace(name="oak_log"),
        }
        with patch.object(
            self.wrapper, "_block_at", side_effect=lambda *position: positions.get(position)
        ):
            logs = self.wrapper._connected_logs((0, 64, 0), "oak_log", 64)

        self.assertEqual(set(logs), set(positions))


class BotInventoryAndCollectionTests(unittest.TestCase):
    def setUp(self):
        self.wrapper = Bot("localhost", "offline", 25565, "Agent", "1.21.11")
        self.wrapper.bot = MagicMock()

    def test_inventory_queries_aggregate_slots(self):
        items = [
            SimpleNamespace(
                name="cobblestone",
                displayName="Cobblestone",
                count=32,
                slot=9,
                type=1,
                metadata=0,
            ),
            SimpleNamespace(
                name="cobblestone",
                displayName="Cobblestone",
                count=4,
                slot=10,
                type=1,
                metadata=0,
            ),
        ]
        self.wrapper.bot.bot.inventory.items.return_value = items

        inventory = self.wrapper.get_inventory()

        self.assertEqual(inventory[0]["display_name"], "Cobblestone")
        self.assertEqual(self.wrapper.get_inventory_counts(), {"cobblestone": 36})
        self.assertEqual(len(self.wrapper.find_inventory_items("cobblestone")), 2)

    def test_equip_best_tool_prefers_strongest_compatible_tier(self):
        wooden_pickaxe = SimpleNamespace(
            name="wooden_pickaxe", displayName="Wooden Pickaxe", count=1,
            slot=9, type=10, metadata=0,
        )
        diamond_pickaxe = SimpleNamespace(
            name="diamond_pickaxe", displayName="Diamond Pickaxe", count=1,
            slot=10, type=11, metadata=0,
        )
        block = SimpleNamespace(
            material="rock", harvestTools={10: True, 11: True}
        )
        self.wrapper.bot.bot.inventory.items.return_value = [
            wooden_pickaxe,
            diamond_pickaxe,
        ]
        self.wrapper.bot.registry.blocksByName = {"stone": block}

        equipped = self.wrapper.equip_best_tool_for_block("stone")

        self.wrapper.bot.bot.equip.assert_called_once_with(diamond_pickaxe, "hand")
        self.assertEqual(equipped["name"], "diamond_pickaxe")

    def test_move_within_reach_navigates_when_needed(self):
        with (
            patch.object(self.wrapper, "is_block_reachable", side_effect=[False, True]),
            patch.object(self.wrapper, "move_to_block") as move_to_block,
        ):
            self.assertTrue(self.wrapper.move_within_reach(1, 2, 3))

        move_to_block.assert_called_once_with(1, 2, 3)

    def test_move_within_reach_falls_back_to_reachable_position(self):
        with (
            patch.object(
                self.wrapper, "is_block_reachable", side_effect=[False, False, True]
            ),
            patch.object(
                self.wrapper,
                "move_to_block",
                side_effect=RuntimeError("pathfinder failed"),
            ),
            patch.object(
                self.wrapper,
                "find_nearest_reachable_position",
                return_value=(2, 3, 4),
            ) as find_reachable,
            patch.object(self.wrapper, "move_near") as move_near,
        ):
            self.assertTrue(self.wrapper.move_within_reach(1, 2, 3))

        find_reachable.assert_called_once_with(1, 2, 3)
        move_near.assert_called_once_with(2, 3, 4, distance=1.5)

    def test_move_within_reach_raises_when_unreachable(self):
        with (
            patch.object(self.wrapper, "is_block_reachable", return_value=False),
            patch.object(self.wrapper, "move_to_block"),
            patch.object(
                self.wrapper, "find_nearest_reachable_position", return_value=None
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "could not move within reach"):
                self.wrapper.move_within_reach(1, 2, 3)

    def test_jump_place_underfoot_uses_pathfinder_tower_and_restores_setting(self):
        item = SimpleNamespace(name="oak_log", type=17)
        reference = SimpleNamespace(name="dirt", boundingBox="block")
        self.wrapper.bot.movements.scafoldingBlocks = []
        self.wrapper.bot.movements.allow1by1towers = False
        with (
            patch.object(
                self.wrapper,
                "_block_position",
                side_effect=[(1, 64, 2), (1, 65, 2)],
            ),
            patch.object(self.wrapper, "_block_at", return_value=reference),
            patch.object(self.wrapper, "_inventory_items", return_value=[item]),
            patch.object(self.wrapper, "move_to") as move_to,
            patch.object(self.wrapper, "wait_for_block_placement") as wait_for_placement,
        ):
            scaffold = self.wrapper._jump_place_underfoot("oak_log")

        move_to.assert_called_once_with(1, 65, 2)
        self.assertEqual(self.wrapper.bot.movements.scafoldingBlocks, [17])
        self.assertFalse(self.wrapper.bot.movements.allow1by1towers)
        wait_for_placement.assert_called_once_with((1, 64, 2), "oak_log", timeout=3.0)
        self.assertEqual(scaffold["position"], (1, 64, 2))

    def test_place_block_equips_item_and_uses_solid_neighbor(self):
        item = SimpleNamespace(name="cobblestone")
        reference = SimpleNamespace(name="stone", boundingBox="block")
        client = MagicMock()
        with (
            patch.object(self.wrapper, "is_replaceable", return_value=True),
            patch.object(self.wrapper, "_inventory_items", return_value=[item]),
            patch.object(self.wrapper, "move_within_reach") as move_within_reach,
            patch.object(self.wrapper, "_block_at", return_value=reference),
            patch.object(self.wrapper, "_client", return_value=client),
            patch.object(self.wrapper, "_vec3", return_value="up") as vec3,
            patch.object(self.wrapper, "look_at_block") as look_at_block,
            patch.object(
                self.wrapper, "wait_for_block_placement"
            ) as wait_for_placement,
        ):
            result = self.wrapper.place_block("cobblestone", 1, 2, 3)

        move_within_reach.assert_called_once_with(1, 2, 3)
        client.equip.assert_called_once_with(item, "hand")
        vec3.assert_called_once_with(0, 1, 0)
        look_at_block.assert_called_once_with(1, 2, 3)
        client.placeBlock.assert_called_once_with(reference, "up")
        wait_for_placement.assert_called_once_with(
            (1, 2, 3), "cobblestone", timeout=3.0
        )
        self.assertEqual(
            result,
            {
                "block_type": "cobblestone",
                "position": {"x": 1.0, "y": 2.0, "z": 3.0},
            },
        )

    def test_place_block_requires_inventory_item(self):
        with (
            patch.object(self.wrapper, "is_replaceable", return_value=True),
            patch.object(self.wrapper, "_inventory_items", return_value=[]),
        ):
            with self.assertRaisesRegex(RuntimeError, "no cobblestone available"):
                self.wrapper.place_block("cobblestone", 1, 2, 3)

    def test_collect_dropped_items_moves_to_new_item(self):
        with (
            patch.object(
                self.wrapper,
                "get_dropped_items",
                side_effect=[[{"id": 7, "distance": 1, "position": {}}], []],
            ),
            patch.object(self.wrapper, "move_to_entity") as move_to_entity,
            patch("src.bot.time.monotonic", side_effect=[0, 0, 0, 10]),
            patch("src.bot.time.sleep"),
        ):
            collected = self.wrapper.collect_dropped_items(timeout=5)

        self.assertEqual(collected, [7])
        move_to_entity.assert_called_once_with(7, distance=0)

    def test_collect_resource_verifies_inventory_gain(self):
        block = SimpleNamespace(type=1)
        counts = [
            {},
            {},
            {"cobblestone": 1},
            {"cobblestone": 1},
            {"cobblestone": 1},
        ]
        with (
            patch.object(self.wrapper, "get_inventory_counts", side_effect=counts),
            patch.object(
                self.wrapper,
                "find_nearest_collectible_block",
                return_value=(1, 2, 3),
            ),
            patch.object(self.wrapper, "move_within_reach") as move_within_reach,
            patch.object(
                self.wrapper,
                "equip_best_tool_for_block",
                return_value={"name": "stone_pickaxe"},
            ),
            patch.object(self.wrapper, "get_required_tool", return_value="pickaxe"),
            patch.object(self.wrapper, "get_dropped_items", return_value=[]),
            patch.object(self.wrapper, "_block_at", return_value=block),
            patch.object(self.wrapper, "mine_block") as mine_block,
            patch.object(self.wrapper, "wait_for_block_change") as wait_for_block_change,
            patch.object(self.wrapper, "move_near") as move_near,
            patch.object(self.wrapper, "collect_dropped_items") as collect_drops,
            patch.object(
                self.wrapper,
                "wait_for_inventory_change",
                return_value={"cobblestone": 1},
            ) as wait_for_change,
        ):
            result = self.wrapper.collect_resource("stone", 1)

        move_within_reach.assert_called_once_with(1, 2, 3)
        mine_block.assert_called_once_with(1, 2, 3)
        wait_for_block_change.assert_called_once_with((1, 2, 3), 1)
        move_near.assert_called_once_with(1.5, 3, 3.5, distance=1.25)
        collect_drops.assert_called_once()
        wait_for_change.assert_called_once_with({}, timeout=5.0)
        self.assertEqual(result["mined"], 1)
        self.assertEqual(result["mined_positions"], [{"x": 1.0, "y": 2.0, "z": 3.0}])
        self.assertEqual(result["gained"], {"cobblestone": 1})

    def test_mine_nearest_tree_pillars_for_high_logs_and_cleans_up(self):
        logs = [(0, 64, 0), (0, 65, 0), (0, 70, 0)]
        block = SimpleNamespace(name="oak_log", type=17)
        elevated = {"value": False}

        def is_reachable(x, y, z):
            return y < 70 or elevated["value"]

        def pillar(*_args, **_kwargs):
            elevated["value"] = True
            return {"type": "oak_log", "position": (1, 64, 0)}

        with (
            patch.object(
                self.wrapper,
                "find_nearest_blocks",
                return_value=[{"type": "oak_log", "position": logs[0]}],
            ),
            patch.object(self.wrapper, "_connected_logs", return_value=logs),
            patch.object(self.wrapper, "move_within_reach") as move_within_reach,
            patch.object(
                self.wrapper,
                "get_inventory_counts",
                side_effect=[{}, {"oak_log": 3}],
            ),
            patch.object(self.wrapper, "is_block_reachable", side_effect=is_reachable),
            patch.object(self.wrapper, "get_position", return_value=(1, 64, 0)),
            patch.object(self.wrapper, "_block_at", side_effect=lambda *p: block if p in logs else None),
            patch.object(self.wrapper, "equip_best_tool_for_block"),
            patch.object(self.wrapper, "mine_block") as mine_block,
            patch.object(self.wrapper, "wait_for_block_change"),
            patch.object(self.wrapper, "collect_dropped_items"),
            patch.object(self.wrapper, "move_near"),
            patch.object(self.wrapper, "_jump_place_underfoot", side_effect=pillar),
            patch.object(self.wrapper, "_remove_scaffold") as remove_scaffold,
        ):
            result = self.wrapper.mine_nearest_tree(radius=64)

        move_within_reach.assert_called_once_with(*logs[0])
        self.assertEqual(mine_block.call_count, 3)
        remove_scaffold.assert_called_once_with(
            {"type": "oak_log", "position": (1, 64, 0)}, timeout=5.0
        )
        self.assertTrue(result["completed"])
        self.assertEqual(result["logs_mined"], 3)

    def test_collectible_block_excludes_support_column(self):
        with (
            patch.object(self.wrapper, "_block_position", return_value=(0, 64, 0)),
            patch.object(
                self.wrapper,
                "find_blocks",
                return_value=[(0, 63, 0), (1, 63, 0)],
            ),
        ):
            position = self.wrapper.find_nearest_collectible_block("grass_block", 32)

        self.assertEqual(position, (1, 63, 0))


class BotCraftingTests(unittest.TestCase):
    def setUp(self):
        self.wrapper = Bot("localhost", "offline", 25565, "Agent", "1.21.11")
        self.wrapper.bot = MagicMock()

    def _recipe(self, result_name, result_id, result_count, requires_table, delta):
        return SimpleNamespace(
            result=SimpleNamespace(id=result_id, count=result_count),
            requiresTable=requires_table,
            delta=delta,
        )

    def _delta(self, item_id, count):
        return SimpleNamespace(id=item_id, count=count)

    def test_get_recipes_summarizes_ingredients_against_inventory(self):
        item = SimpleNamespace(id=270)
        self.wrapper.bot.registry.itemsByName = {"wooden_pickaxe": item}
        self.wrapper.bot.registry.items = {
            270: SimpleNamespace(name="wooden_pickaxe"),
            5: SimpleNamespace(name="oak_planks"),
            280: SimpleNamespace(name="stick"),
        }
        recipe = self._recipe(
            "wooden_pickaxe",
            270,
            1,
            True,
            [self._delta(5, -3), self._delta(280, -2), self._delta(270, 1)],
        )
        self.wrapper.bot.bot.recipesAll.return_value = [recipe]
        with patch.object(
            self.wrapper,
            "get_inventory_counts",
            return_value={"oak_planks": 3, "stick": 1},
        ):
            recipes = self.wrapper.get_recipes("wooden_pickaxe")

        self.assertEqual(len(recipes), 1)
        summary = recipes[0]
        self.assertEqual(summary["result_name"], "wooden_pickaxe")
        self.assertEqual(summary["result_count"], 1)
        self.assertTrue(summary["requires_crafting_table"])
        self.assertEqual(summary["crafts_possible"], 0)
        ingredients = {entry["name"]: entry for entry in summary["ingredients"]}
        self.assertEqual(ingredients["oak_planks"]["required"], 3)
        self.assertEqual(ingredients["oak_planks"]["missing"], 0)
        self.assertEqual(ingredients["stick"]["required"], 2)
        self.assertEqual(ingredients["stick"]["missing"], 1)
        self.wrapper.bot.bot.recipesAll.assert_called_once_with(270, None, None)

    def test_get_recipes_rejects_unknown_item(self):
        self.wrapper.bot.registry.itemsByName = {}

        with self.assertRaises(KeyError):
            self.wrapper.get_recipes("unknown_item")

    def test_craft_item_uses_inventory_grid_when_table_not_required(self):
        item = SimpleNamespace(id=5)
        self.wrapper.bot.registry.itemsByName = {"oak_planks": item}
        self.wrapper.bot.registry.items = {
            17: SimpleNamespace(name="oak_log"),
        }
        recipe = self._recipe("oak_planks", 5, 4, False, [self._delta(17, -1)])
        self.wrapper.bot.bot.recipesAll.return_value = [recipe]
        with (
            patch.object(
                self.wrapper, "get_inventory_counts", side_effect=[{"oak_log": 1}, {"oak_planks": 4}]
            ),
            patch.object(self.wrapper, "_find_crafting_table_block", return_value=None),
        ):
            result = self.wrapper.craft_item("oak_planks", 1)

        self.assertEqual(result["crafted"], 4)
        self.assertEqual(result["inventory_count"], 4)
        self.wrapper.bot.bot.recipesAll.assert_called_once_with(5, None, None)
        self.wrapper.bot.bot.craft.assert_called_once_with(recipe, 1, None)

    def test_craft_item_selects_recipe_matching_inventory(self):
        item = SimpleNamespace(id=270)
        self.wrapper.bot.registry.itemsByName = {"wooden_pickaxe": item}
        self.wrapper.bot.registry.items = {
            17: SimpleNamespace(name="birch_planks"),
            5: SimpleNamespace(name="oak_planks"),
        }
        birch_recipe = self._recipe(
            "wooden_pickaxe", 270, 1, True, [self._delta(17, -3)]
        )
        oak_recipe = self._recipe(
            "wooden_pickaxe", 270, 1, True, [self._delta(5, -3)]
        )
        self.wrapper.bot.bot.recipesAll.return_value = [birch_recipe, oak_recipe]
        table_block = SimpleNamespace(position=SimpleNamespace(x=1, y=64, z=0))
        counts = {"oak_planks": 3}
        with (
            patch.object(
                self.wrapper,
                "get_inventory_counts",
                side_effect=[counts, {"oak_planks": 3, "wooden_pickaxe": 1}],
            ),
            patch.object(
                self.wrapper, "_find_crafting_table_block", return_value=table_block
            ),
            patch.object(self.wrapper, "move_within_reach"),
            patch.object(self.wrapper, "_block_at", return_value=table_block),
        ):
            result = self.wrapper.craft_item("wooden_pickaxe", 1)

        self.assertEqual(result["crafted"], 1)
        self.wrapper.bot.bot.craft.assert_called_once_with(
            oak_recipe, 1, table_block
        )

    def test_craft_item_places_table_when_required_and_missing(self):
        item = SimpleNamespace(id=270)
        self.wrapper.bot.registry.itemsByName = {"wooden_pickaxe": item}
        self.wrapper.bot.registry.items = {
            5: SimpleNamespace(name="oak_planks"),
        }
        recipe = self._recipe("wooden_pickaxe", 270, 1, True, [self._delta(5, -3)])
        self.wrapper.bot.bot.recipesAll.side_effect = [[], [recipe]]
        self.wrapper.bot.bot.inventory.items.return_value = [
            SimpleNamespace(name="crafting_table")
        ]
        table_block = SimpleNamespace(position=SimpleNamespace(x=1, y=64, z=0))
        with (
            patch.object(
                self.wrapper,
                "get_inventory_counts",
                side_effect=[{"oak_planks": 3}, {"oak_planks": 3, "wooden_pickaxe": 1}],
            ),
            patch.object(
                self.wrapper, "_find_crafting_table_block", return_value=None
            ),
            patch.object(
                self.wrapper, "_place_crafting_table", return_value=table_block
            ) as place_table,
            patch.object(self.wrapper, "move_within_reach") as move_within_reach,
            patch.object(self.wrapper, "_block_at", return_value=table_block),
        ):
            result = self.wrapper.craft_item("wooden_pickaxe", 1)

        place_table.assert_called_once_with()
        move_within_reach.assert_called_once_with(1, 64, 0)
        self.assertEqual(result["crafted"], 1)
        self.wrapper.bot.bot.craft.assert_called_once_with(recipe, 1, table_block)

    def test_craft_item_reports_missing_ingredients(self):
        item = SimpleNamespace(id=270)
        self.wrapper.bot.registry.itemsByName = {"wooden_pickaxe": item}
        self.wrapper.bot.registry.items = {
            5: SimpleNamespace(name="oak_planks"),
        }
        recipe = self._recipe("wooden_pickaxe", 270, 1, True, [self._delta(5, -3)])
        self.wrapper.bot.bot.recipesAll.return_value = [recipe]
        self.wrapper.bot.bot.inventory.items.return_value = []
        with (
            patch.object(self.wrapper, "get_inventory_counts", return_value={}),
            patch.object(self.wrapper, "_find_crafting_table_block", return_value=None),
        ):
            with self.assertRaisesRegex(RuntimeError, "missing ingredients.*oak_planks"):
                self.wrapper.craft_item("wooden_pickaxe", 1)

        self.wrapper.bot.bot.craft.assert_not_called()

    def test_craft_item_errors_when_no_recipe(self):
        item = SimpleNamespace(id=999)
        self.wrapper.bot.registry.itemsByName = {"unknown": item}
        self.wrapper.bot.bot.recipesAll.return_value = []
        self.wrapper.bot.bot.inventory.items.return_value = []
        with (
            patch.object(self.wrapper, "get_inventory_counts", return_value={}),
            patch.object(self.wrapper, "_find_crafting_table_block", return_value=None),
        ):
            with self.assertRaisesRegex(RuntimeError, "no recipe available"):
                self.wrapper.craft_item("unknown", 1)

    def test_craft_item_rejects_invalid_quantity(self):
        with self.assertRaisesRegex(ValueError, "quantity must be a positive integer"):
            self.wrapper.craft_item("oak_planks", 0)

    def test_craft_thread_propagates_craft_errors(self):
        recipe = SimpleNamespace()
        self.wrapper._craft_thread = None
        self.wrapper._craft_error = None
        self.wrapper.bot.bot.craft.side_effect = RuntimeError("missing ingredient")

        self.wrapper.start_crafting(recipe, 1, None)
        with self.assertRaisesRegex(RuntimeError, "missing ingredient"):
            self.wrapper.finish_crafting()

        self.assertIsNone(self.wrapper._craft_thread)
        self.assertIsNone(self.wrapper._craft_error)

    def test_craft_times_out_when_still_running(self):
        import threading as _threading

        recipe = SimpleNamespace()
        self.wrapper._craft_thread = None
        self.wrapper._craft_error = None
        blocker = _threading.Event()
        self.wrapper.bot.bot.craft.side_effect = lambda *_a, **_k: blocker.wait(5)

        self.wrapper.start_crafting(recipe, 1, None)
        with self.assertRaisesRegex(TimeoutError, "did not finish"):
            self.wrapper.finish_crafting(timeout=0.05)

        blocker.set()


class BotEatingTests(unittest.TestCase):
    def setUp(self):
        self.wrapper = Bot("localhost", "offline", 25565, "Agent", "1.21.11")
        self.wrapper.bot = MagicMock()
        self.wrapper._eat_thread = None
        self.wrapper._eat_error = None
        self.wrapper.bot.mc_data.foods = [
            SimpleNamespace(
                name="apple", foodPoints=4, saturation=2.4, effectiveQuality=6.4
            ),
            SimpleNamespace(
                name="cooked_beef",
                foodPoints=8,
                saturation=12.8,
                effectiveQuality=20.8,
            ),
        ]

    def _inventory(self, *items):
        self.wrapper.bot.bot.inventory.items.return_value = list(items)

    def test_eat_selects_highest_quality_food(self):
        apple = SimpleNamespace(name="apple")
        beef = SimpleNamespace(name="cooked_beef")
        self._inventory(apple, beef)
        with patch.object(self.wrapper, "get_food_level", return_value=12.0):
            result = self.wrapper.eat()

        client = self.wrapper.bot.bot
        client.equip.assert_called_once_with(beef, "hand")
        client.consume.assert_called_once_with()
        self.assertEqual(result, {"eaten": "cooked_beef", "food_level": 12.0})

    def test_eat_specific_item(self):
        apple = SimpleNamespace(name="apple")
        self._inventory(apple)
        with patch.object(self.wrapper, "get_food_level", return_value=10.0):
            result = self.wrapper.eat("apple")

        self.wrapper.bot.bot.equip.assert_called_once_with(apple, "hand")
        self.assertEqual(result["eaten"], "apple")

    def test_eat_errors_when_no_food(self):
        self._inventory(SimpleNamespace(name="cobblestone"))

        with self.assertRaisesRegex(RuntimeError, "no food available"):
            self.wrapper.eat()

    def test_eat_errors_when_item_not_in_inventory(self):
        self._inventory(SimpleNamespace(name="apple"))

        with self.assertRaisesRegex(RuntimeError, "no bread available"):
            self.wrapper.eat("bread")

    def test_eat_errors_when_item_is_not_food(self):
        self._inventory(SimpleNamespace(name="cobblestone"))

        with self.assertRaisesRegex(RuntimeError, "cobblestone is not a food item"):
            self.wrapper.eat("cobblestone")

    def test_eat_rejects_invalid_item_name(self):
        with self.assertRaisesRegex(ValueError, "item_name must be a non-empty"):
            self.wrapper.eat("")

    def test_eat_propagates_consume_errors(self):
        apple = SimpleNamespace(name="apple")
        self._inventory(apple)
        self.wrapper.bot.bot.consume.side_effect = RuntimeError("Food is full")
        with patch.object(self.wrapper, "get_food_level", return_value=20.0):
            with self.assertRaisesRegex(RuntimeError, "Food is full"):
                self.wrapper.eat("apple")

        self.wrapper.bot.bot.deactivateItem.assert_called_once_with()
        self.assertIsNone(self.wrapper._eat_thread)

    def test_eat_times_out_when_still_running(self):
        apple = SimpleNamespace(name="apple")
        self._inventory(apple)
        blocker = threading.Event()
        self.wrapper.bot.bot.consume.side_effect = lambda: blocker.wait(5)

        self.wrapper.start_eating(apple)
        with self.assertRaisesRegex(TimeoutError, "did not finish"):
            self.wrapper.finish_eating(timeout=0.05)
        self.wrapper.cancel_eating()

        self.assertIsNone(self.wrapper._eat_thread)
        self.assertIsNone(self.wrapper._eat_error)
        self.wrapper.bot.bot.deactivateItem.assert_called_once_with()
        blocker.set()


if __name__ == "__main__":
    unittest.main()
