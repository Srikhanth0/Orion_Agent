"""
agents/planner.py — LangGraph planner node.

Uses the 70B model with structured output to decompose complex user
requests into a checklist of concrete, atomic subtasks (max 8).
"""
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

import config
from agents.prompts import build_planner_prompt
from agents.state import AgentState
from utils.logger import get_logger

logger = get_logger(__name__)


class PlanChecklist(BaseModel):
    """Structured output schema for the planner."""
    subtasks: list[str] = Field(
        description="Ordered list of atomic action steps (max 8).",
        max_length=8,
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Brief explanation of the plan rationale.",
    )


async def planner_node(state: AgentState) -> dict:
    """
    Generate a multi-step checklist for a complex task.

    Uses with_structured_output() to guarantee a valid PlanChecklist schema
    from the 70B model. Converts subtask strings into checklist dicts.

    Returns:
        {"checklist": [...], "status": "executing", "current_subtask_index": 0}
    """
    llm = config.get_llm()
    task = state.get("task", "")
    memory_context = state.get("memory_context", "")

    logger.info("Planner generating checklist for: %.80s...", task)

    # Build the planning prompt with optional memory context
    planning_messages = [
        SystemMessage(content=build_planner_prompt()),
    ]

    if memory_context:
        planning_messages.append(
            SystemMessage(
                content=f"Relevant context from past tasks:\n{memory_context}"
            )
        )

    planning_messages.append(HumanMessage(content=task))

    try:
        # Use structured output for guaranteed schema compliance
        structured_llm = llm.with_structured_output(PlanChecklist)
        plan: PlanChecklist = await structured_llm.ainvoke(planning_messages)

        subtasks = plan.subtasks[:8]  # Hard cap at 8 steps
        logger.info("Checklist generated with %d subtasks", len(subtasks))
        for i, step in enumerate(subtasks):
            logger.info("  Subtask %d: %s", i + 1, step)

        # Convert to checklist format
        checklist = [
            {"subtask": s, "status": "pending", "attempts": 0}
            for s in subtasks
        ]

        return {
            "checklist": checklist,
            "status": "executing",
            "current_subtask_index": 0,
        }

    except Exception as exc:
        logger.error("Planner failed: %s — falling back to single-step", exc)
        # Fallback: treat the whole task as a single subtask
        return {
            "checklist": [
                {"subtask": task, "status": "pending", "attempts": 0}
            ],
            "status": "executing",
            "current_subtask_index": 0,
        }
