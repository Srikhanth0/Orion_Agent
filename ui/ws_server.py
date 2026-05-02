"""ui/ws_server.py — WebSocket bridge between ORION agent and the desktop widget.

Provides:
  - Chat: user_message → agent pipeline → agent_response
  - Logs: broadcast_log() pushes log lines to all connected widgets
  - Stats: dashboard metrics (task count, success rate, uptime, MCP status)
  - Config: settings updates from widget → orion_config.json
"""
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import websockets

from utils.logger import get_logger

logger = get_logger(__name__)

# ── State ─────────────────────────────────────────────────────────────────────
_connected_clients: set = set()
_agent_graph: Any = None
_mcp_client: Any = None
_start_time: float = time.time()
_task_count: int = 0
_success_count: int = 0
_total_duration_ms: int = 0
_recent_tasks: list[dict] = []

# Config file path (relative to project root)
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "orion_config.json"


def set_graph(graph: Any) -> None:
    """Set the compiled agent graph (called from main.py at startup)."""
    global _agent_graph
    _agent_graph = graph


def set_mcp_client(client: Any) -> None:
    """Set the MCP client for stats reporting."""
    global _mcp_client
    _mcp_client = client


# ── Broadcast Helpers ─────────────────────────────────────────────────────────

async def broadcast_log(level: str, msg: str) -> None:
    """Send a log line to all connected widget clients."""
    if not _connected_clients:
        return
    data = json.dumps({"type": "log", "level": level, "msg": msg})
    await asyncio.gather(
        *[_safe_send(c, data) for c in list(_connected_clients)],
        return_exceptions=True,
    )


async def broadcast_stats() -> None:
    """Send dashboard stats to all connected widget clients."""
    if not _connected_clients:
        return

    uptime_s = int(time.time() - _start_time)
    success_rate = (
        round(_success_count / _task_count * 100, 1) if _task_count > 0 else 0.0
    )
    avg_duration = (
        round(_total_duration_ms / _task_count) if _task_count > 0 else 0
    )

    # MCP server status
    mcp_servers = []
    if _mcp_client:
        for name in _mcp_client.get_server_names():
            connected = _mcp_client.is_connected(name)
            mcp_servers.append({
                "name": name,
                "status": "online" if connected else "offline",
            })

    data = json.dumps({
        "type": "stats",
        "tasks_run": _task_count,
        "success_rate": success_rate,
        "avg_duration_ms": avg_duration,
        "uptime_seconds": uptime_s,
        "mcp_servers": mcp_servers,
        "recent_tasks": _recent_tasks[-5:],
    })
    await asyncio.gather(
        *[_safe_send(c, data) for c in list(_connected_clients)],
        return_exceptions=True,
    )


async def _safe_send(ws, data: str) -> None:
    """Send data to a WebSocket, removing it from clients if it fails."""
    try:
        await ws.send(data)
    except Exception:
        _connected_clients.discard(ws)


def record_task(task: str, success: bool, duration_ms: int = 0) -> None:
    """Record a completed task for stats (called from the memory node)."""
    global _task_count, _success_count, _total_duration_ms
    _task_count += 1
    if success:
        _success_count += 1
    _total_duration_ms += duration_ms
    _recent_tasks.append({
        "task": task[:100],
        "success": success,
        "time": time.strftime("%H:%M:%S"),
    })
    # Cap at 20 entries
    if len(_recent_tasks) > 20:
        _recent_tasks.pop(0)


# ── Config I/O ────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    """Load settings from orion_config.json."""
    if _CONFIG_PATH.exists():
        try:
            return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_config(cfg: dict) -> None:
    """Save settings to orion_config.json."""
    _CONFIG_PATH.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── WebSocket Handler ─────────────────────────────────────────────────────────

async def _ws_handler(websocket, path=None) -> None:
    """Handle a single widget WebSocket connection."""
    _connected_clients.add(websocket)
    client_id = id(websocket)
    logger.info("Widget connected (client %d, total: %d)", client_id, len(_connected_clients))

    try:
        # Send initial stats on connect
        await broadcast_stats()

        async for raw in websocket:
            try:
                msg = json.loads(raw)
                msg_type = msg.get("type", "")

                if msg_type == "user_message":
                    await _handle_user_message(websocket, msg.get("text", ""))

                elif msg_type == "get_stats":
                    await broadcast_stats()

                elif msg_type == "config_update":
                    cfg = _load_config()
                    cfg[msg["key"]] = msg["value"]
                    _save_config(cfg)
                    logger.info("Config updated: %s = %s", msg["key"], msg["value"])

                elif msg_type == "get_config":
                    cfg = _load_config()
                    await websocket.send(json.dumps({"type": "config", **cfg}))

            except json.JSONDecodeError:
                logger.warning("Widget sent invalid JSON")
            except Exception as exc:
                logger.error("Widget message handler error: %s", exc)

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        _connected_clients.discard(websocket)
        logger.info("Widget disconnected (client %d, remaining: %d)", client_id, len(_connected_clients))


async def _handle_user_message(websocket, text: str) -> None:
    """Route a user message through the agent graph and send the response."""
    if not text.strip():
        return

    if _agent_graph is None:
        await websocket.send(json.dumps({
            "type": "agent_response",
            "text": "ORION agent is not ready yet. Please wait...",
        }))
        return

    try:
        from langchain_core.messages import HumanMessage

        start = time.time()
        state = {
            "messages": [HumanMessage(content=text)],
            "user_id": "widget_user",
        }
        result = await _agent_graph.ainvoke(
            state,
            config={
                "configurable": {"thread_id": "widget_session"},
                "recursion_limit": 100,
            },
        )
        duration_ms = int((time.time() - start) * 1000)

        messages = result.get("messages", [])
        response_text = messages[-1].content if messages else "No response generated."

        # Record for stats
        tool_results = result.get("tool_results", [])
        success = all(r.get("success", False) for r in tool_results) if tool_results else True
        record_task(text, success, duration_ms)

        await websocket.send(json.dumps({
            "type": "agent_response",
            "text": response_text,
        }))
        await broadcast_stats()

    except Exception as exc:
        logger.error("Agent execution failed: %s", exc)
        await websocket.send(json.dumps({
            "type": "agent_response",
            "text": f"Error: {exc}",
        }))


# ── Server Startup ────────────────────────────────────────────────────────────

async def start_ws_server(host: str = "localhost", port: int = 8765) -> None:
    """Start the WebSocket server for the desktop widget."""
    logger.info("Starting ORION WebSocket server on ws://%s:%d", host, port)
    async with websockets.serve(_ws_handler, host, port):
        await asyncio.Future()  # Run forever
