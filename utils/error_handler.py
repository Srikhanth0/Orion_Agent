"""
utils/error_handler.py — Centralised error handling for tool calls.

Provides a decorator and exception classes so that every tool call
returns a structured dict instead of raising unhandled exceptions.
"""
import asyncio
import functools
import traceback
from typing import Any, Callable

from utils.logger import get_logger

logger = get_logger(__name__)


class RetryableError(Exception):
    """An error that the executor should retry (up to max_retries)."""
    pass


class FatalError(Exception):
    """An unrecoverable error — skip retries and report to user."""
    pass


def safe_tool_call(func: Callable) -> Callable:
    """
    Decorator that wraps a sync or async tool function so it never
    raises to the caller.  Returns a dict:
      {"success": True,  "result": <value>}
      {"success": False, "error": <message>, "retryable": bool}
    """

    @functools.wraps(func)
    async def _async_wrapper(*args: Any, **kwargs: Any) -> dict:
        try:
            result = await func(*args, **kwargs)
            return {"success": True, "result": result}
        except FatalError as exc:
            logger.error("Fatal error in %s: %s", func.__name__, exc)
            return {"success": False, "error": str(exc), "retryable": False}
        except RetryableError as exc:
            logger.warning("Retryable error in %s: %s", func.__name__, exc)
            return {"success": False, "error": str(exc), "retryable": True}
        except Exception as exc:
            logger.error(
                "Unexpected error in %s: %s\n%s",
                func.__name__,
                exc,
                traceback.format_exc(),
            )
            return {"success": False, "error": str(exc), "retryable": True}

    @functools.wraps(func)
    def _sync_wrapper(*args: Any, **kwargs: Any) -> dict:
        try:
            result = func(*args, **kwargs)
            return {"success": True, "result": result}
        except FatalError as exc:
            logger.error("Fatal error in %s: %s", func.__name__, exc)
            return {"success": False, "error": str(exc), "retryable": False}
        except RetryableError as exc:
            logger.warning("Retryable error in %s: %s", func.__name__, exc)
            return {"success": False, "error": str(exc), "retryable": True}
        except Exception as exc:
            logger.error(
                "Unexpected error in %s: %s\n%s",
                func.__name__,
                exc,
                traceback.format_exc(),
            )
            return {"success": False, "error": str(exc), "retryable": True}

    if asyncio.iscoroutinefunction(func):
        return _async_wrapper
    return _sync_wrapper
