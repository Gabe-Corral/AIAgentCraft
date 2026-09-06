"""Run the Ollama planner against the WebSocket bot server."""

import argparse
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.logging_setup import configure_logging
from src.planner import BotWebSocketClient, OllamaClient, Planner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("goal", help="Minecraft goal for the bot to complete")
    parser.add_argument(
        "--model", default=os.environ.get("OLLAMA_MODEL", "qwen3")
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
    )
    parser.add_argument(
        "--bot-url",
        default=os.environ.get("BOT_WEBSOCKET_URL", "ws://127.0.0.1:8765"),
    )
    parser.add_argument(
        "--ollama-timeout",
        type=float,
        default=float(os.environ.get("OLLAMA_TIMEOUT", "300")),
        help="seconds to wait for each Ollama response",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=int(os.environ.get("PLANNER_MAX_STEPS", "30")),
    )
    args = parser.parse_args()

    configure_logging()
    ollama = OllamaClient(
        args.model, base_url=args.ollama_url, timeout=args.ollama_timeout
    )
    with BotWebSocketClient(args.bot_url) as bot:
        result = Planner(bot, ollama, max_steps=args.max_steps).run(args.goal)
    print(result)


if __name__ == "__main__":
    main()
