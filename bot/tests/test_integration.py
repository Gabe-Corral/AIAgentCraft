import os
import unittest

from javascript import terminate

from src.bot import AIR_BLOCKS, Bot
from src.config import load_minecraft_config


MINECRAFT_CONFIG = load_minecraft_config()


@unittest.skipUnless(
    os.environ.get("MINECRAFT_INTEGRATION") == "1",
    "set MINECRAFT_INTEGRATION=1 to run live-server tests",
)
class BotLiveServerTests(unittest.TestCase):
    @staticmethod
    def move_outside_spawn_protection(bot):
        radius = int(os.environ.get("MINECRAFT_SPAWN_PROTECTION", "16"))
        search_radius = int(os.environ.get("MINECRAFT_TEST_RADIUS", "32"))
        if radius <= 0:
            return
        spawn = bot.bot.spawn_point
        x, y, z = bot.get_position()
        safe_distance = radius + search_radius + 4
        if max(abs(x - spawn.x), abs(z - spawn.z)) <= safe_distance:
            bot.move_near(spawn.x + safe_distance, y, spawn.z, distance=1.5)
        x, _, z = bot.get_position()
        if max(abs(x - spawn.x), abs(z - spawn.z)) <= radius + search_radius:
            raise AssertionError("test search area overlaps spawn protection")

    def test_collect_resource_increases_inventory(self):
        bot = Bot(
            host=MINECRAFT_CONFIG.host,
            auth=MINECRAFT_CONFIG.auth,
            port=MINECRAFT_CONFIG.port,
            username=MINECRAFT_CONFIG.username,
            version=MINECRAFT_CONFIG.version,
        )
        try:
            bot.connect()
            self.assertTrue(bot.wait_until_spawned(timeout=30))
            self.assertEqual(bot.get_game_mode(), "survival")
            self.move_outside_spawn_protection(bot)

            result = bot.collect_resource(
                os.environ.get("MINECRAFT_TEST_BLOCK", "grass_block"),
                quantity=1,
                radius=int(os.environ.get("MINECRAFT_TEST_RADIUS", "32")),
                timeout=10,
            )

            self.assertEqual(result["mined"], 1)
            self.assertGreater(result["gained"].get("dirt", 0), 0)
            mined_position = result["mined_positions"][0]
            remaining = bot.get_block(
                mined_position["x"], mined_position["y"], mined_position["z"]
            )
            self.assertIn(str(remaining.name), AIR_BLOCKS)
        finally:
            bot.shutdown()
            terminate()

    def test_craft_wooden_pickaxe(self):
        bot = Bot(
            host=MINECRAFT_CONFIG.host,
            auth=MINECRAFT_CONFIG.auth,
            port=MINECRAFT_CONFIG.port,
            username=MINECRAFT_CONFIG.username,
            version=MINECRAFT_CONFIG.version,
        )
        try:
            bot.connect()
            self.assertTrue(bot.wait_until_spawned(timeout=30))
            self.assertEqual(bot.get_game_mode(), "survival")
            self.move_outside_spawn_protection(bot)

            # Ensure we have enough logs for the full chain:
            # 1 log -> 4 planks -> (crafting table + sticks) -> wooden pickaxe.
            if bot.get_inventory_counts().get("oak_log", 0) < 2:
                bot.collect_resource("oak_log", quantity=2, radius=64, timeout=15)

            # Craft planks from logs (inventory grid).
            if bot.get_inventory_counts().get("oak_planks", 0) < 4:
                bot.craft_item("oak_planks", 1)

            # Craft a crafting table if we don't already have one placed nearby.
            if bot.get_inventory_counts().get("crafting_table", 0) < 1:
                bot.craft_item("crafting_table", 1)

            # Craft sticks (needs planks).
            if bot.get_inventory_counts().get("stick", 0) < 2:
                bot.craft_item("stick", 1)

            # Craft the target tool; this must place/use a crafting table.
            result = bot.craft_item("wooden_pickaxe", 1)

            self.assertEqual(result["crafted"], 1)
            counts = bot.get_inventory_counts()
            self.assertGreaterEqual(counts.get("wooden_pickaxe", 0), 1)
        finally:
            bot.shutdown()
            terminate()


if __name__ == "__main__":
    unittest.main()
