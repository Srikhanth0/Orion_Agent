"""
memory/task_history.py — SQLite integration for task analytics.

Logs all completed tasks with their execution status and duration.
"""
import time
from pathlib import Path
from sqlite_utils import Database

import config
from utils.logger import get_logger

logger = get_logger(__name__)

_db = None


def _get_db() -> Database:
    """Initialize and return the SQLite database lazily."""
    global _db
    
    if _db is not None:
        return _db

    db_path = Path(config.SQLITE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    logger.info("Initializing SQLite task history at %s", db_path)
    _db = Database(str(db_path))
    
    # Create table if it doesn't exist
    if "tasks" not in _db.table_names():
        _db["tasks"].create({
            "id": int,
            "user_id": str,
            "task": str,
            "status": str,
            "duration_ms": int,
            "created_at": float
        }, pk="id")
        
    return _db


async def log_task(user_id: str, task: str, status: str, duration_ms: int = 0) -> None:
    """
    Log a completed task to SQLite.
    
    Args:
        user_id: The identifier of the user who requested the task.
        task: The task description.
        status: e.g., 'success', 'partial', 'error'.
        duration_ms: Execution duration in milliseconds.
    """
    try:
        db = _get_db()
        db["tasks"].insert({
            "user_id": user_id,
            "task": task,
            "status": status,
            "duration_ms": duration_ms,
            "created_at": time.time()
        })
        logger.debug("Logged task to history (status: %s)", status)
    except Exception as exc:
        logger.error("Failed to log task to SQLite: %s", exc)


async def get_recent_tasks(limit: int = 10, user_id: str = "") -> list[dict]:
    """Retrieve the most recent tasks from SQLite."""
    try:
        db = _get_db()
        where = "user_id = :user_id" if user_id else None
        params = {"user_id": user_id} if user_id else None
        
        return list(db["tasks"].rows_where(
            where, params, order_by="created_at desc", limit=limit
        ))
    except Exception as exc:
        logger.error("Failed to retrieve recent tasks: %s", exc)
        return []
