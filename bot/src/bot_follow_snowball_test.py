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


def find_snowball(bot):
    items = bot._client().inventory.items()
    try:
        length = len(items.valueOf())
    except Exception:
        length = len(items)

    for index in range(length):
        item = items[index]
        if str(item.name) == "snowball":
            return item
    return None


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
        print(f"Following {TARGET_USERNAME} and throwing available snowballs. Press Ctrl+C to stop.")

        while True:
            snowball = find_snowball(bot)
            if snowball is not None:
                bot.look_at_entity(target.id)
                bot._client().equip(snowball, "hand")
                bot._client().activateItem()
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        bot.shutdown()


if __name__ == "__main__":
    main()
