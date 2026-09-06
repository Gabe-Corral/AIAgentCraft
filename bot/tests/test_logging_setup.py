import logging
import os
import unittest
from unittest.mock import patch

from src.logging_setup import configure_logging


class LoggingSetupTests(unittest.TestCase):
    def tearDown(self):
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def test_default_level_is_info(self):
        with patch.dict(os.environ, {}, clear=True):
            root = configure_logging()

        self.assertEqual(root.level, logging.INFO)

    def test_env_var_overrides_default(self):
        with patch.dict(os.environ, {"LOG_LEVEL": "debug"}):
            root = configure_logging()

        self.assertEqual(root.level, logging.DEBUG)

    def test_explicit_level_wins_over_env_var(self):
        with patch.dict(os.environ, {"LOG_LEVEL": "DEBUG"}):
            root = configure_logging("warning")

        self.assertEqual(root.level, logging.WARNING)

    def test_reconfiguration_replaces_handlers(self):
        configure_logging()
        handlers_before = list(logging.getLogger().handlers)

        configure_logging("DEBUG")

        self.assertEqual(len(logging.getLogger().handlers), len(handlers_before))


if __name__ == "__main__":
    unittest.main()
