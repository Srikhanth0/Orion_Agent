"""ui/log_streamer.py — Async log file tail for real-time UI streaming."""
import asyncio
from pathlib import Path


async def tail_log_file(log_path: str, poll_interval: float = 0.3):
    """Async generator that yields new lines from a log file as they appear.

    Args:
        log_path: Absolute or relative path to the log file.
        poll_interval: Seconds between poll attempts when no new data.

    Yields:
        Each new line (stripped of trailing whitespace) as it is appended.
    """
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(0, 2)  # Seek to end of file
        while True:
            line = f.readline()
            if line:
                yield line.rstrip()
            else:
                await asyncio.sleep(poll_interval)
