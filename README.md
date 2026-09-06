# Minecraft Agent

Python Minecraft bot primitives and a JSON request/response API served over
WebSockets. See [PROTOCOL.md](PROTOCOL.md) for the wire protocol and
[ROADMAP.md](ROADMAP.md) for implementation status.

## Prerequisites

- Python 3.12
- Node.js 18 or newer (required by Lodestone/Mineflayer)
- Java compatible with the Minecraft server in `game_server`
- Ollama with a tool-calling model (required for the planner)

## Setup

From the repository root:

```bash
cd bot
python3.12 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
```

Lodestone installs its required Node packages when the bot first starts.

Minecraft connection settings are stored in the repository-root `config.toml`.

## Run The Bot Server

Start Minecraft in one terminal:

```bash
cd game_server
./run_server.sh
```

Start the bot and WebSocket server in another terminal:

```bash
cd bot
./venv/bin/python -m src.server
```

The WebSocket server listens on `ws://127.0.0.1:8765` by default. Operational
configuration can be overridden with:

- `WEBSOCKET_HOST`
- `WEBSOCKET_PORT`
- `LOG_LEVEL` defaults to `INFO`; use `DEBUG` for verbose output.
- `NAVIGATION_TIMEOUT` defaults to `30` seconds.
- `NAVIGATION_STUCK_TIMEOUT` defaults to `5` seconds without position progress.
- `NAVIGATION_POLL_INTERVAL` defaults to `0.1` seconds.
- `NAVIGATION_MIN_PROGRESS` defaults to `0.1` blocks.

## Run The Planner

With Minecraft, the bot WebSocket server, and Ollama running, pull a
tool-calling model and give the planner a goal:

```bash
ollama pull qwen3
cd bot
./venv/bin/python scripts/run_planner.py "collect one oak log"
```

Planner configuration can be overridden with:

- `OLLAMA_MODEL` defaults to `qwen3`.
- `OLLAMA_URL` defaults to `http://127.0.0.1:11434`.
- `OLLAMA_TIMEOUT` defaults to `300` seconds per planning step.
- `BOT_WEBSOCKET_URL` defaults to `ws://127.0.0.1:8765`.
- `PLANNER_MAX_STEPS` defaults to `30`.

The planner observes the initial state, asks Ollama for tool calls, executes
allowlisted calls over the WebSocket protocol, and returns tool failures to the
model so it can revise its plan. It stops when the model gives a final response
or the step limit is reached.

## Tests

Run the complete default suite from `bot`:

```bash
./venv/bin/python -m unittest discover -s tests -v
```

This runs bot, protocol, and real WebSocket transport tests. The test suite uses
mocks for Minecraft behavior and opens only an ephemeral loopback WebSocket
port. The live Minecraft test is discovered but skipped unless explicitly
enabled.

Run individual suites:

```bash
./venv/bin/python -m unittest discover -s tests -p 'test_bot.py' -v
./venv/bin/python -m unittest discover -s tests -p 'test_protocol.py' -v
./venv/bin/python -m unittest discover -s tests -p 'test_planner.py' -v
./venv/bin/python -m unittest discover -s tests -p 'test_server.py' -v
```

### Live Minecraft Test

The live test connects a dedicated `CollectTest` bot to a running server, mines
one block, collects its drop, and verifies that inventory increased. It modifies
the test world.

1. Start `game_server/run_server.sh`.
2. Run:

```bash
cd bot
MINECRAFT_INTEGRATION=1 \
  ./venv/bin/python -m unittest discover -s tests -p 'test_integration.py' -v
```

The live test uses the Minecraft connection settings from `config.toml`.
Optional live-test configuration:

- `MINECRAFT_TEST_BLOCK` defaults to `grass_block`.
- `MINECRAFT_TEST_RADIUS` defaults to `32`.
- `MINECRAFT_SPAWN_PROTECTION` defaults to `16`; the test moves outside this
  radius plus the search radius before mining. Set it to the server's configured
  value.

Run every test, including the live test, with:

```bash
cd bot
MINECRAFT_INTEGRATION=1 ./venv/bin/python -m unittest discover -s tests -v
```

The Minecraft server must already be running for this command.

## Manual Test Scripts

`bot/scripts` contains interactive live-server scripts that are not part of the
automated suite. They require a running Minecraft server and modify the world.
Run them from `bot`, for example:

```bash
./venv/bin/python scripts/bot_follow_test.py
./venv/bin/python scripts/mine_tree_craft_pickaxe_test.py
```
