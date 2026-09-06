import asyncio
import json
import logging
import os

from websockets.server import serve

from src.bot import Bot
from src.config import load_minecraft_config
from src.logging_setup import configure_logging
from src.protocol import handle_request


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def _is_interrupt_request(message):
    try:
        request = json.loads(message)
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(request, dict) and request.get("method") in {
        "stop_movement",
        "stop_navigation",
    }


class BotWebSocketServer:
    """WebSocket transport for the bot request/response protocol."""

    def __init__(self, bot, host="127.0.0.1", port=8765):
        self.bot = bot
        self.host = host
        self.port = port
        self._command_lock = None
        self._server = None

    async def _handle_connection(self, websocket):
        logger.info("client connected from %s", websocket.remote_address)
        try:
            async for message in websocket:
                if not isinstance(message, str):
                    message = None
                if _is_interrupt_request(message):
                    response = await asyncio.to_thread(handle_request, self.bot, message)
                else:
                    async with self._command_lock:
                        response = await asyncio.to_thread(handle_request, self.bot, message)
                await websocket.send(response)
                result = json.loads(response).get("result", {})
                if isinstance(result, dict) and result.get("disconnected") is True:
                    await websocket.close(code=1000, reason="client requested disconnect")
                    break
        finally:
            logger.info("client disconnected from %s", websocket.remote_address)

    async def start(self):
        if self._server is not None:
            raise RuntimeError("WebSocket server is already running")
        self._command_lock = asyncio.Lock()
        self._server = await serve(
            self._handle_connection,
            self.host,
            self.port,
            max_size=1_048_576,
        )
        self.port = self._server.sockets[0].getsockname()[1]
        logger.info("websocket server listening on ws://%s:%s", self.host, self.port)
        return self

    async def stop(self):
        if self._server is None:
            return False
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        return True

    async def serve_forever(self):
        await self.start()
        try:
            await self._server.serve_forever()
        finally:
            await self.stop()


def _bot_from_config(config=None):
    if config is None:
        config = load_minecraft_config()
    return Bot(
        host=config.host,
        auth=config.auth,
        port=config.port,
        username=config.username,
        version=config.version,
        navigation_timeout=float(os.environ.get("NAVIGATION_TIMEOUT", "30")),
        navigation_stuck_timeout=float(
            os.environ.get("NAVIGATION_STUCK_TIMEOUT", "5")
        ),
        navigation_poll_interval=float(
            os.environ.get("NAVIGATION_POLL_INTERVAL", "0.1")
        ),
        navigation_min_progress=float(
            os.environ.get("NAVIGATION_MIN_PROGRESS", "0.1")
        ),
    )


def main():
    configure_logging()
    bot = _bot_from_config()
    try:
        bot.connect()
        if not bot.wait_until_spawned(timeout=30):
            raise TimeoutError("bot did not spawn within 30 seconds")
        server = BotWebSocketServer(
            bot,
            host=os.environ.get("WEBSOCKET_HOST", "127.0.0.1"),
            port=int(os.environ.get("WEBSOCKET_PORT", "8765")),
        )
        asyncio.run(server.serve_forever())
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("shutting down")
        bot.shutdown()


if __name__ == "__main__":
    main()
