"""agents/graph.py — LangGraph StateGraph assembly (V3 — Calibrator + Validator).

7-node graph with calibrator and validator in the execution loop:
  START → supervisor → calibrator → planner/executor → validator → executor (loop)
                                                                → memory → responder → END
"""
import functools
from typing import Any

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

import config
from agents.state import AgentState
from agents.supervisor import supervisor_node
from agents.planner import planner_node
from agents.calibrator import calibrator_node
from agents.executor import executor_node
from agents.validator import validator_node
from agents.responder import responder_node
from utils.logger import get_logger

logger = get_logger(__name__)


async def memory_node(state: AgentState) -> dict:
    """
    Background node: embed successful task context into ChromaDB.

    Non-blocking — failures here do not affect the response pipeline.
    """
    try:
        from memory.vector_store import embed_task
        from memory.task_history import log_task

        task = state.get("task", "")
        tool_results = state.get("tool_results", [])
        user_id = state.get("user_id", "")

        if task and tool_results:
            # Build a summary of what happened
            summary = " | ".join(
                f"{r.get('tool', '?')}: {r.get('summary', '')[:100]}"
                for r in tool_results
                if r.get("success")
            )

            # Store in vector DB for semantic retrieval
            await embed_task(task, summary, user_id)

            # Log to SQLite for analytics
            success = all(r.get("success", False) for r in tool_results)
            await log_task(
                user_id=user_id,
                task=task,
                status="success" if success else "partial",
            )

            logger.info("Memory node: stored task context for '%.60s...'", task)

    except Exception as exc:
        # Memory failures are non-critical — log and continue
        logger.warning("Memory node failed (non-critical): %s", exc)

    return {"status": state.get("status", "done")}


def _route_after_supervisor(state: AgentState) -> str:
    """Route from supervisor to calibrator or responder based on intent."""
    intent = state.get("intent", "simple_task")
    if intent == "chat":
        return "responder"
    # Both simple_task and complex_task go through calibrator first
    return "calibrator"


def _route_after_calibrator(state: AgentState) -> str:
    """Route from calibrator to planner (complex) or executor (simple)."""
    intent = state.get("intent", "simple_task")
    if intent == "complex_task":
        return "planner"
    return "executor"


def _route_after_validator(state: AgentState) -> str:
    """
    Route from validator based on checklist state:
    - All subtasks completed → memory (then responder)
    - Error/max retries → responder (report failure)
    - More subtasks or retry needed → executor
    """
    status = state.get("status", "executing")
    checklist = state.get("checklist", [])
    current_idx = state.get("current_subtask_index", 0)

    if status == "done":
        return "memory"

    if status == "error":
        logger.warning("Routing to responder due to error/max retries")
        return "responder"

    if current_idx >= len(checklist):
        return "memory"

    # Still have subtasks to execute or retrying
    return "executor"


def build_graph(tools: list[Any] | None = None, mcp_client: Any = None) -> Any:
    """
    Build and compile the LangGraph StateGraph with all nodes and edges.

    Args:
        tools: List of LangChain-compatible tool objects to inject into
               the executor node.
        mcp_client: The MultiMCPClient instance (for validator screenshots
                    and calibrator screen detection).

    Returns:
        A compiled LangGraph runnable (with MemorySaver checkpointer).
    """
    tools = tools or []

    # Create partials with injected dependencies
    executor_with_tools = functools.partial(executor_node, tools=tools)
    validator_with_mcp = functools.partial(validator_node, mcp_client=mcp_client)
    calibrator_with_mcp = functools.partial(calibrator_node, mcp_client=mcp_client)

    # Build the graph
    graph = StateGraph(AgentState)

    # Add nodes (7 total)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("calibrator", calibrator_with_mcp)
    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_with_tools)
    graph.add_node("validator", validator_with_mcp)
    graph.add_node("memory", memory_node)
    graph.add_node("responder", responder_node)

    # ── Edges ──────────────────────────────────────────────────────────────

    # START → supervisor
    graph.add_edge(START, "supervisor")

    # supervisor → calibrator OR responder (chat bypasses everything)
    graph.add_conditional_edges(
        "supervisor",
        _route_after_supervisor,
        {"calibrator": "calibrator", "responder": "responder"},
    )

    # calibrator → planner (complex) OR executor (simple)
    graph.add_conditional_edges(
        "calibrator",
        _route_after_calibrator,
        {"planner": "planner", "executor": "executor"},
    )

    # planner → executor
    graph.add_edge("planner", "executor")

    # executor → validator (always goes through QA)
    graph.add_edge("executor", "validator")

    # validator → executor (retry/next) OR memory (done) OR responder (error)
    graph.add_conditional_edges(
        "validator",
        _route_after_validator,
        {
            "executor": "executor",
            "memory": "memory",
            "responder": "responder",
        },
    )

    # memory → responder
    graph.add_edge("memory", "responder")

    # responder → END
    graph.add_edge("responder", END)

    # Compile with checkpointer for thread-based memory
    checkpointer = MemorySaver()
    compiled = graph.compile(checkpointer=checkpointer)

    logger.info(
        "Graph compiled: 7 nodes (with calibrator + validator), %d tools injected",
        len(tools),
    )
    return compiled
