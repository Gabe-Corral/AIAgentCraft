import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.bot import Bot
from src.config import load_minecraft_config


TARGET_USERNAME = "NobleGreeb"


def find_player_entity(bot, username, timeout=60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        player = bot.bot.players[username]
        if player is not None and player.entity is not None:
            return player.entity
        time.sleep(0.5)
    raise TimeoutError(f"Could not find player {username!r} within {timeout} seconds")


def main():
    config = load_minecraft_config()
    bot = Bot(
        host=config.host,
        auth=config.auth,
        port=config.port,
        username=config.username,
        version=config.version,
    )

    try:
        bot.connect()
        if not bot.wait_until_spawned():
            raise TimeoutError("Bot did not spawn within 30 seconds")

        target = find_player_entity(bot, TARGET_USERNAME)
        bot.follow_entity(target.id)
        print(f"Following {TARGET_USERNAME}. Press Ctrl+C to stop.")

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        bot.shutdown()


if __name__ == "__main__":
    main()
