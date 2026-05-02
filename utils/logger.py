"""utils/logger.py — Structured logging for the Windows Assistant.

Provides a pre-configured logger with both console (coloured) and
file output. Includes a WebSocket broadcast handler that pushes
log lines to connected widget clients.

Import `get_logger(__name__)` in every module.
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


class _WebSocketBroadcastHandler(logging.Handler):
    """Pushes log records to all connected widget clients via WebSocket.

    Non-blocking: creates a task in the running event loop.
    Falls back silently if no event loop or no clients.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            import asyncio
            from ui.ws_server import broadcast_log

            msg = self.format(record)
            level = record.levelname.replace("\033[32m", "").replace(
                "\033[33m", "").replace("\033[31m", "").replace(
                "\033[35m", "").replace("\033[36m", "").replace(
                "\033[0m", "").strip()

            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(broadcast_log(level, msg))
        except Exception:
            pass  # Never crash the logger


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

    # File handler (always enabled)
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(str(path), encoding="utf-8")
        fh.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        root.addHandler(fh)

    # WebSocket broadcast handler (for widget log streaming)
    ws_handler = _WebSocketBroadcastHandler()
    ws_handler.setFormatter(logging.Formatter("%(name)s | %(message)s"))
    ws_handler.setLevel(logging.INFO)
    root.addHandler(ws_handler)

    _INITIALIZED = True


def get_logger(name: str, level: str | None = None) -> logging.Logger:
    """
    Return a named logger.  Initialises the root logger on first call
    using the LOG_LEVEL and LOG_FILE_PATH from config.
    """
    import os

    if not _INITIALIZED:
        env_level = os.getenv("LOG_LEVEL", "INFO")
        log_file = os.getenv("LOG_FILE_PATH", None)
        _init_root(level=env_level, log_file=log_file)

    logger = logging.getLogger(name)
    if level:
        logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger
