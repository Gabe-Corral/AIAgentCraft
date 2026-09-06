import tomllib
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.toml"


@dataclass(frozen=True)
class MinecraftConfig:
    host: str
    auth: str
    port: int
    username: str
    version: str


def load_minecraft_config(path=DEFAULT_CONFIG_PATH):
    with Path(path).open("rb") as config_file:
        return MinecraftConfig(**tomllib.load(config_file)["minecraft"])
