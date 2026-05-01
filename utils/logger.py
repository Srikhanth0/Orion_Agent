"""
utils/logger.py — Structured logging for the Windows Assistant.

Provides a pre-configured logger with both console (coloured) and
optional file output.  Import `get_logger(__name__)` in every module.
"""
import logging
import sys
from pathlib import Path


_INITIALIZED = False
_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-28s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ANSI colours for console output
_COLOURS = {
    "DEBUG": "\033[36m",     # cyan
    "INFO": "\033[32m",      # green
    "WARNING": "\033[33m",   # yellow
    "ERROR": "\033[31m",     # red
    "CRITICAL": "\033[35m",  # magenta
}
_RESET = "\033[0m"


class _ColourFormatter(logging.Formatter):
    """Adds ANSI colour codes to log-level names on the console."""

    def format(self, record: logging.LogRecord) -> str:
        colour = _COLOURS.get(record.levelname, "")
        record.levelname = f"{colour}{record.levelname}{_RESET}"
        return super().format(record)


def _init_root(level: str = "INFO", log_file: str | None = None) -> None:
    """Configure the root logger once."""
    global _INITIALIZED
    if _INITIALIZED:
        return

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Console handler (coloured)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(_ColourFormatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    root.addHandler(console)

    # Optional file handler (plain text)
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(str(path), encoding="utf-8")
        fh.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        root.addHandler(fh)

    _INITIALIZED = True


def get_logger(name: str, level: str | None = None) -> logging.Logger:
    """
    Return a named logger.  Initialises the root logger on first call
    using the LOG_LEVEL env-var (default INFO).
    """
    import os

    if not _INITIALIZED:
        env_level = os.getenv("LOG_LEVEL", "INFO")
        _init_root(level=env_level)

    logger = logging.getLogger(name)
    if level:
        logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger
