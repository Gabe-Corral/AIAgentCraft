import tempfile
import unittest
from pathlib import Path

from src.config import load_minecraft_config


class MinecraftConfigTests(unittest.TestCase):
    def test_loads_minecraft_table(self):
        contents = """\
[minecraft]
host = "minecraft.test"
auth = "offline"
port = 25570
username = "TestBot"
version = "1.21.11"
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(contents)
            config = load_minecraft_config(path)

        self.assertEqual(config.host, "minecraft.test")
        self.assertEqual(config.port, 25570)
        self.assertEqual(config.username, "TestBot")


if __name__ == "__main__":
    unittest.main()
