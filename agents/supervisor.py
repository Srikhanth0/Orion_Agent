"""
agents/supervisor.py — LangGraph supervisor node (V3 — 3-way intent).

Uses the fast 8B model to classify incoming requests into:
  - "chat"         → route DIRECTLY to responder (skip planner/executor)
  - "simple_task"  → route to executor with single-item checklist
  - "complex_task" → route to planner for multi-step checklist

Examples:
  "hi" / "are you ready?" / "thanks" → chat
  "open Notepad" / "take a screenshot" → simple_task
  "send an email summarizing today's calendar" → complex_task
"""
import json

from langchain_core.messages import HumanMessage, SystemMessage

import config
from agents.prompts import build_supervisor_prompt
from agents.state import AgentState
from utils.logger import get_logger

logger = get_logger(__name__)


async def supervisor_node(state: AgentState) -> dict:
    """
    Classify the user's request into chat / simple_task / complex_task.

    Returns:
        For chat:         {"intent": "chat", "status": "done", "task": ...}
        For simple_task:  {"intent": "simple_task", "status": "executing", "checklist": [...]}
        For complex_task: {"intent": "complex_task", "status": "planning", "task": ...}
    """
    llm = config.get_fast_llm()

    # Extract the latest user message as the task
    task = ""
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            task = msg.content
            break

    if not task:
        logger.warning("No user message found in state; defaulting to error")
        return {"status": "error", "task": "", "intent": "chat"}

    logger.info("Supervisor routing task: %.80s...", task)

    try:
        response = await llm.ainvoke([
            SystemMessage(content=build_supervisor_prompt()),
            HumanMessage(content=task),
        ])

        content = response.content.strip()
        # Handle potential markdown code blocks in response
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        result = json.loads(content)
        intent = result.get("intent", "simple_task")

        # Normalize legacy classification field
        if "classification" in result and "intent" not in result:
            legacy = result["classification"]
            if legacy == "simple":
                intent = "simple_task"
            elif legacy == "complex":
                intent = "complex_task"
            else:
                intent = "chat"

    except (json.JSONDecodeError, KeyError, Exception) as exc:
        logger.warning(
            "Supervisor classification failed (%s), defaulting to 'simple_task'", exc
        )
        intent = "simple_task"

    # ── Route based on intent ──────────────────────────────────────────────

    if intent == "chat":
        logger.info("Intent: CHAT -> routing directly to responder")
        return {
            "intent": "chat",
            "status": "done",
            "task": task,
            "checklist": [],
            "error_count": 0,
            "current_subtask_index": 0,
            "tool_results": [],
        }

    elif intent == "complex_task":
        logger.info("Intent: COMPLEX_TASK -> routing to planner")
        return {
            "intent": "complex_task",
            "status": "planning",
            "task": task,
            "error_count": 0,
            "current_subtask_index": 0,
            "tool_results": [],
            "checklist": [],
        }

    else:  # simple_task
        logger.info("Intent: SIMPLE_TASK -> routing to executor")
        return {
            "intent": "simple_task",
            "status": "executing",
            "task": task,
            "checklist": [
                {"subtask": task, "status": "pending", "attempts": 0}
            ],
            "error_count": 0,
            "current_subtask_index": 0,
            "tool_results": [],
        }
