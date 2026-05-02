"""agents/planner.py — LangGraph planner node (V3 — Physical Decomposition).

Uses the 70B model with structured output to decompose complex user
requests into PHYSICAL, atomic subtasks using the action grammar.
Auto-validates checklist dependencies (SNAPSHOT before CLICK, etc.).
"""
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

import config
from agents.prompts import build_task_planner_prompt, build_memory_context_prompt
from agents.state import AgentState
from utils.logger import get_logger

logger = get_logger(__name__)


class PlanChecklist(BaseModel):
    """Structured output schema for the physical planner."""
    subtasks: list[str] = Field(
        description="Ordered list of PHYSICAL atomic action steps (max 8).",
        max_length=8,
    )
    domain: Optional[str] = Field(
        default="mixed",
        description="Primary domain: os_gui, browser_dom, api, or mixed.",
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Brief explanation of the plan rationale.",
    )


def _validate_checklist_dependencies(subtasks: list[str]) -> list[str]:
    """
    Inject missing prerequisite steps in the checklist.

    Rules enforced:
      - FIND or CLICK_AT must be preceded by a SNAPSHOT
      - VERIFY must be the final step of any sequence
    """
    validated = []
    last_snapshot_idx = -1

    for i, step in enumerate(subtasks):
        step_upper = step.upper()

        # Auto-inject SNAPSHOT before FIND or CLICK_AT if missing
        if ("FIND " in step_upper or "CLICK_AT" in step_upper):
            if last_snapshot_idx < len(validated) - 1:
                validated.append("SNAPSHOT (verify current screen state)")
                last_snapshot_idx = len(validated) - 1

        if "SNAPSHOT" in step_upper:
            last_snapshot_idx = len(validated)

        validated.append(step)

    # Ensure VERIFY is the last step
    if validated and "VERIFY" not in validated[-1].upper():
        validated.append("VERIFY expected final state is visible on screen")

    return validated


async def planner_node(state: AgentState) -> dict:
    """
    Generate a multi-step checklist for a complex task.

    Uses with_structured_output() to guarantee a valid PlanChecklist schema
    from the 70B model. Runs physical dependency validation after generation.

    Returns:
        {"checklist": [...], "status": "executing", "current_subtask_index": 0}
    """
    llm = config.get_llm()
    task = state.get("task", "")
    memory_context = state.get("memory_context", "")
    screen_width = state.get("screen_width", 1920)
    screen_height = state.get("screen_height", 1080)

    logger.info("Planner generating checklist for: %.80s...", task)

    # Build the planning prompt with screen dimensions
    planning_messages = [
        SystemMessage(content=build_task_planner_prompt(
            screen_width=screen_width,
            screen_height=screen_height,
        )),
    ]

    # Add memory context using structured prompt
    memory_prompt = build_memory_context_prompt(memory_context)
    if memory_prompt:
        planning_messages.append(SystemMessage(content=memory_prompt))

    planning_messages.append(HumanMessage(content=task))

    try:
        # Use structured output for guaranteed schema compliance
        structured_llm = llm.with_structured_output(PlanChecklist)
        plan: PlanChecklist = await structured_llm.ainvoke(planning_messages)

        subtasks = plan.subtasks[:8]  # Hard cap at 8 steps

        # Validate physical dependencies
        subtasks = _validate_checklist_dependencies(subtasks)
        subtasks = subtasks[:10]  # Allow up to 10 after injection

        logger.info("Checklist generated with %d physical subtasks", len(subtasks))
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
