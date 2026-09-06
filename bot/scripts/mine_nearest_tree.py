import json
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.logging_setup import configure_logging
from src.server import _bot_from_config


def mine_nearest_tree(bot, radius=128, max_logs=64):
    return bot.mine_nearest_tree(radius=radius, max_logs=max_logs)


def main():
    configure_logging()
    radius = int(os.environ.get("MINECRAFT_TREE_RADIUS", "128"))
    max_logs = int(os.environ.get("MINECRAFT_TREE_MAX_LOGS", "64"))
    bot = _bot_from_config()

    try:
        bot.connect()
        if not bot.wait_until_spawned(timeout=30):
            raise TimeoutError("bot did not spawn within 30 seconds")
        result = mine_nearest_tree(bot, radius=radius, max_logs=max_logs)
        print(json.dumps(result, indent=2))
    finally:
        bot.shutdown()


if __name__ == "__main__":
    main()
