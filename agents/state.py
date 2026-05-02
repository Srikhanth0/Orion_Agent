"""
agents/state.py — LangGraph state schema (V3 — Sensory-Motor Architecture).

Every node reads from and writes to this typed dict.
The state flows through:
  supervisor → (chat → responder) OR (planner → executor ⇄ validator → memory → responder)

Checklist schema per subtask:
  {"subtask": str, "status": "pending"|"completed"|"failed", "attempts": int}
"""
from typing import Annotated, Literal
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """Central state object shared across all graph nodes."""

    # Full conversation history — add_messages handles append-only updates
    messages: Annotated[list[BaseMessage], add_messages]

    # Intent classification from supervisor
    intent: Literal["chat", "complex_task", "simple_task"]

    # The current user task extracted from the latest message
    task: str

    # Checklist of subtasks with per-item status tracking
    # Schema: [{"subtask": str, "status": "pending"|"completed"|"failed", "attempts": int}]
    checklist: list[dict]

    # Index into the checklist (0-based); advanced by the validator
    current_subtask_index: int

    # Accumulated results from each tool execution
    tool_results: list[dict]

    # Semantic memory context retrieved from ChromaDB (past similar tasks)
    memory_context: str

    # Current pipeline status — drives conditional edges
    status: Literal["routing", "planning", "executing", "validating", "done", "error"]

    # Retry counter — executor increments on failure, caps at 3
    error_count: int

    # The Telegram / Slack / UI user ID for access control and memory isolation
    user_id: str

    # Screen geometry (set by calibrator_node, consumed by prompts)
    screen_width: int          # Primary monitor width in pixels
    screen_height: int         # Primary monitor height in pixels
    dpi_scale: float           # DPI scale factor (1.0 = 96 DPI)
    calibrated: bool           # Whether calibrator has run for this task

    # Preferred MCP server for this task domain (set by supervisor)
    mcp_hint: str              # e.g. "windows", "playwright", "google", "fincept"

    # Speed optimization: skip vision validation for non-GUI tasks
    skip_vision: bool          # True = skip screenshot validation for API/file tasks

