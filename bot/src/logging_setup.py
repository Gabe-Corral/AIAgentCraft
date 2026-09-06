import logging
import os


DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level=None):
    """Configure root logging from an explicit level or the LOG_LEVEL env var."""
    level_name = str(level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(format=DEFAULT_LOG_FORMAT, level=level_name, force=True)
    return logging.getLogger()
