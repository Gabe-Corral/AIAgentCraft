import math
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from src.main import Bot


class BotMovementTests(unittest.TestCase):
    def setUp(self):
        self.wrapper = Bot.__new__(Bot)
        self.wrapper.bot = MagicMock()

    @patch("src.main.time.sleep")
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

    @patch("src.main.time.sleep", side_effect=RuntimeError("interrupted"))
    def test_timed_movement_releases_control_when_interrupted(self, _sleep):
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.wrapper.walk_forward(1)

        self.wrapper.bot.set_control_state.assert_has_calls(
            [call("forward", True), call("forward", False)]
        )

    @patch("src.main.time.sleep")
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
        self.wrapper = Bot.__new__(Bot)
        self.wrapper.bot = MagicMock()

    def assert_navigates_to(self, goal):
        self.wrapper.bot.pathfinder.goto.assert_called_once_with(
            goal, timeout=600_000_000
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

    @patch("src.main.random.random", return_value=1)
    @patch("src.main.random.uniform", return_value=0)
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

    def test_stop_navigation_stops_pathfinder(self):
        self.wrapper.stop_navigation()

        self.wrapper.bot.pathfinder.stop.assert_called_once_with()


class BotLifecycleTests(unittest.TestCase):
    def test_init_stores_configuration_without_connecting(self):
        wrapper = Bot("example.test", "offline", 25565, "Agent", "1.21.11")

        self.assertEqual(wrapper.host, "example.test")
        self.assertEqual(wrapper.username, "Agent")
        self.assertFalse(wrapper.is_connected())

    @patch("src.main.lodestone.createBot")
    def test_connect_and_disconnect(self, create_bot):
        lodestone_bot = MagicMock()
        lodestone_bot.entity = None

        def register(_event):
            return lambda callback: callback

        lodestone_bot.on.side_effect = register
        create_bot.return_value = lodestone_bot
        wrapper = Bot("old-host", "offline", 25565, "OldName", "1.21.11")

        wrapper.connect("new-host", 25566, "NewName")

        create_bot.assert_called_once_with(
            host="new-host",
            auth="offline",
            port=25566,
            username="NewName",
            version="1.21.11",
        )
        lodestone_bot.chat.assert_called_once()
        self.assertTrue(wrapper.is_connected())

        self.assertTrue(wrapper.disconnect())
        lodestone_bot.bot.end.assert_called_once_with()
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

    def test_mine_block_waits_for_dig(self):
        block = SimpleNamespace(name="stone", diggable=True)
        client = MagicMock()
        self.wrapper._dig_thread = None
        self.wrapper._dig_error = None
        self.wrapper._digging_position = None
        with (
            patch.object(self.wrapper, "_block_at", return_value=block),
            patch.object(self.wrapper, "look_at_block"),
            patch.object(self.wrapper, "_client", return_value=client),
        ):
            self.assertTrue(self.wrapper.mine_block(1, 2, 3))

        client.dig.assert_called_once_with(block)
        self.assertIsNone(self.wrapper._digging_position)


if __name__ == "__main__":
    unittest.main()
