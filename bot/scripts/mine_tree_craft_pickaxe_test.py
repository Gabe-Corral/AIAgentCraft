"""Manual live-server test: fell the nearest tree, then craft a wooden pickaxe.

Requires a running Minecraft server. Run from the repository-root bot directory:

    ./venv/bin/python scripts/mine_tree_craft_pickaxe_test.py
"""

import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.mine_nearest_tree import mine_nearest_tree
from src.logging_setup import configure_logging
from src.server import _bot_from_config


def main():
    configure_logging()
    bot = _bot_from_config()
    try:
        bot.connect()
        if not bot.wait_until_spawned(timeout=30):
            raise TimeoutError("bot did not spawn within 30 seconds")

        # Fell trees until we hold enough logs for the whole chain. A wooden
        # pickaxe needs 3 planks + 2 sticks; the crafting table needs 4 planks
        # and sticks take 2 planks, so 3 plank crafts (3 logs) yield enough.
        # Tall trees leave unreachable canopy logs, so one tree may not suffice.
        trees = []
        while True:
            counts = bot.get_inventory_counts()
            held_logs = {
                name: count
                for name, count in counts.items()
                if name.endswith("_log")
            }
            if sum(held_logs.values()) >= 3:
                break
            tree = mine_nearest_tree(bot)
            trees.append(tree)
            if tree["logs_mined"] == 0:
                raise RuntimeError("no reachable logs on any nearby tree")

        # Use whichever log type we actually hold the most of.
        log_type = max(held_logs, key=held_logs.get)
        planks_type = log_type.replace("_log", "_planks")

        planks = bot.craft_item(planks_type, 3)
        table = bot.craft_item("crafting_table", 1)
        sticks = bot.craft_item("stick", 1)
        pickaxe = bot.craft_item("wooden_pickaxe", 1)

        print(
            json.dumps(
                {
                    "trees": trees,
                    "planks": planks,
                    "crafting_table": table,
                    "sticks": sticks,
                    "wooden_pickaxe": pickaxe,
                    "inventory": bot.get_inventory_counts(),
                },
                indent=2,
            )
        )
    finally:
        bot.shutdown()


if __name__ == "__main__":
    main()
