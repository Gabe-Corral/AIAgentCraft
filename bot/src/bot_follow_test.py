import time

from main import Bot


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
    bot = Bot(
        host="localhost",
        auth="offline",
        port=25565,
        username="BotName",
        version="1.21.11",
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
