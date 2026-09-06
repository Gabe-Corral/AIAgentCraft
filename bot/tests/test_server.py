import asyncio
import json
import threading
import unittest
from unittest.mock import MagicMock, patch

from websockets.client import connect
from websockets.exceptions import ConnectionClosedOK

from src.config import MinecraftConfig
from src.server import BotWebSocketServer, _bot_from_config


class BotConfigurationTests(unittest.TestCase):
    @patch("src.server.Bot")
    def test_minecraft_config_and_navigation_environment_are_loaded(self, bot_class):
        environment = {
            "NAVIGATION_TIMEOUT": "45",
            "NAVIGATION_STUCK_TIMEOUT": "8",
            "NAVIGATION_POLL_INTERVAL": "0.2",
            "NAVIGATION_MIN_PROGRESS": "0.25",
        }
        config = MinecraftConfig(
            host="minecraft.test",
            auth="offline",
            port=25570,
            username="TestBot",
            version="1.21.11",
        )

        with patch.dict("os.environ", environment, clear=True):
            _bot_from_config(config)

        bot_class.assert_called_once_with(
            host="minecraft.test",
            auth="offline",
            port=25570,
            username="TestBot",
            version="1.21.11",
            navigation_timeout=45.0,
            navigation_stuck_timeout=8.0,
            navigation_poll_interval=0.2,
            navigation_min_progress=0.25,
        )


class BotWebSocketServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = MagicMock()
        self.server = BotWebSocketServer(self.bot, port=0)
        await self.server.start()
        self.uri = f"ws://127.0.0.1:{self.server.port}"

    async def asyncTearDown(self):
        await self.server.stop()

    async def send(self, request):
        async with connect(self.uri) as websocket:
            await websocket.send(json.dumps(request))
            return json.loads(await websocket.recv())

    async def test_ping_round_trip(self):
        response = await self.send(
            {"version": 1, "id": "socket-1", "method": "ping", "params": {}}
        )

        self.assertEqual(
            response,
            {
                "version": 1,
                "id": "socket-1",
                "ok": True,
                "result": {"pong": True},
            },
        )

    async def test_status_round_trip(self):
        self.bot.get_status.return_value = {"connected": True, "action": "idle"}

        response = await self.send(
            {"version": 1, "id": "socket-2", "method": "get_status"}
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["action"], "idle")

    async def test_observe_round_trip(self):
        self.bot.observe.return_value = {"nearby": {"entities": []}}

        response = await self.send(
            {"version": 1, "id": "socket-observe", "method": "observe"}
        )

        self.assertEqual(response["result"], {"nearby": {"entities": []}})
        self.bot.observe.assert_called_once_with()

    async def test_collect_resource_round_trip(self):
        self.bot.collect_resource.return_value = {"mined": 2, "gained": {"oak_log": 2}}

        response = await self.send(
            {
                "version": 1,
                "id": "socket-collect",
                "method": "collect_resource",
                "params": {"block_type": "oak_log", "quantity": 2},
            }
        )

        self.assertEqual(response["result"]["mined"], 2)
        self.bot.collect_resource.assert_called_once_with("oak_log", 2, 32, 5.0)

    async def test_inventory_query_round_trip(self):
        self.bot.get_inventory_counts.return_value = {"torch": 12}

        response = await self.send(
            {"version": 1, "id": "socket-inventory", "method": "get_inventory_counts"}
        )

        self.assertEqual(response["result"], {"torch": 12})

    async def test_list_result_keeps_connection_open(self):
        self.bot.get_inventory.return_value = [{"name": "torch", "count": 1}]

        async with connect(self.uri) as websocket:
            await websocket.send(
                json.dumps(
                    {"version": 1, "id": "socket-list", "method": "get_inventory"}
                )
            )
            inventory = json.loads(await websocket.recv())
            await websocket.send(
                json.dumps({"version": 1, "id": "socket-after-list", "method": "ping"})
            )
            ping = json.loads(await websocket.recv())

        self.assertEqual(inventory["result"], [{"name": "torch", "count": 1}])
        self.assertTrue(ping["ok"])

    async def test_navigation_action_round_trip(self):
        response = await self.send(
            {
                "version": 1,
                "id": "socket-move",
                "method": "move_near",
                "params": {"x": 1, "y": 64, "z": 3, "distance": 2},
            }
        )

        self.assertEqual(response["result"], {"completed": True})
        self.bot.move_near.assert_called_once_with(1, 64, 3, distance=2)

    async def test_place_block_round_trip(self):
        self.bot.place_block.return_value = {
            "block_type": "cobblestone",
            "position": {"x": 1, "y": 63, "z": 3},
        }

        response = await self.send(
            {
                "version": 1,
                "id": "socket-place",
                "method": "place_block",
                "params": {
                    "block_type": "cobblestone",
                    "x": 1,
                    "y": 63,
                    "z": 3,
                },
            }
        )

        self.assertEqual(response["result"]["block_type"], "cobblestone")
        self.bot.place_block.assert_called_once_with(
            "cobblestone", 1, 63, 3, timeout=3.0
        )

    async def test_stop_navigation_interrupts_active_command(self):
        navigation_started = threading.Event()
        navigation_stopped = threading.Event()

        def move_to(*_args):
            navigation_started.set()
            navigation_stopped.wait(1)

        self.bot.move_to.side_effect = move_to
        self.bot.stop_navigation.side_effect = navigation_stopped.set

        move_request = asyncio.create_task(
            self.send(
                {
                    "version": 1,
                    "id": "socket-long-move",
                    "method": "move_to",
                    "params": {"x": 10, "y": 64, "z": 10},
                }
            )
        )
        started = await asyncio.to_thread(navigation_started.wait, 1)
        self.assertTrue(started)
        stop_response = await self.send(
            {"version": 1, "id": "socket-stop", "method": "stop_navigation"}
        )
        move_response = await move_request

        self.assertEqual(stop_response["result"], {"completed": True})
        self.assertEqual(move_response["result"], {"completed": True})
        self.assertTrue(navigation_stopped.is_set())

    async def test_disconnect_acknowledges_then_closes_connection(self):
        async with connect(self.uri) as websocket:
            await websocket.send(
                json.dumps(
                    {"version": 1, "id": "socket-close", "method": "disconnect"}
                )
            )
            response = json.loads(await websocket.recv())

            self.assertEqual(response["result"], {"disconnected": True})
            with self.assertRaises(ConnectionClosedOK):
                await websocket.recv()

    async def test_malformed_request_returns_error_without_closing_connection(self):
        async with connect(self.uri) as websocket:
            await websocket.send("{")
            error = json.loads(await websocket.recv())
            await websocket.send(
                json.dumps({"version": 1, "id": "socket-3", "method": "ping"})
            )
            success = json.loads(await websocket.recv())

        self.assertEqual(error["error"]["code"], "INVALID_REQUEST")
        self.assertTrue(success["ok"])

    async def test_server_lifecycle_is_idempotent(self):
        with self.assertRaisesRegex(RuntimeError, "already running"):
            await self.server.start()

        self.assertTrue(await self.server.stop())
        self.assertFalse(await self.server.stop())
