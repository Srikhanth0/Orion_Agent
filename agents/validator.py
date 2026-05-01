"""
agents/validator.py — LangGraph validator node (Vision QA Layer).

The critical QA step that verifies each subtask actually succeeded:
  1. Triggers the Windows MCP Screenshot tool to capture current screen
  2. Passes the base64 screenshot + subtask goal to the Vision LLM (Gemma 4 31B via OpenRouter)
  3. Asks: "Did this action succeed based on the screen?"
  4. YES → marks subtask "completed", advances index
  5. NO → marks "failed", increments attempts
  6. attempts > 3 → sets status "error" to abort

Graceful degradation:
  - If Vision LLM is unavailable or rate-limited, falls back to tool-result-based validation
  - If screenshot fails, falls back to tool-result-based validation
"""
import base64
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

import config
from agents.state import AgentState
from utils.logger import get_logger

logger = get_logger(__name__)

# Vision validation prompt
_VALIDATION_PROMPT = """You are a QA validator for a Windows desktop automation agent.
You will receive a screenshot of the current screen state and a description of the action that was just attempted.

Your job: Determine if the action succeeded based on visual evidence in the screenshot.

Rules:
1. Look for visual confirmation that the described action completed (e.g., app opened, page loaded, file created).
2. If you cannot determine success from the screenshot alone, lean towards YES if the action seems plausible.
3. Be concise in your reasoning.

Respond in EXACTLY this format:
RESULT: YES or NO
REASON: <one sentence explanation>"""


async def validator_node(state: AgentState, mcp_client: Any = None) -> dict:
    """
    Validate the most recently executed subtask using Vision LLM + screenshot.

    Args:
        state: Current agent state with checklist and tool_results.
        mcp_client: The MultiMCPClient instance for taking screenshots.

    Returns:
        Updated state with checklist status changes and routing.
    """
    checklist = list(state.get("checklist", []))
    current_idx = state.get("current_subtask_index", 0)
    tool_results = state.get("tool_results", [])
    max_retries = config.AGENT_MAX_RETRIES

    # Guard: no checklist or index out of bounds
    if not checklist or current_idx >= len(checklist):
        logger.info("Validator: no subtasks to validate, marking done")
        return {"status": "done"}

    current_subtask = checklist[current_idx]
    subtask_desc = current_subtask.get("subtask", "")
    attempts = current_subtask.get("attempts", 0)

    logger.info(
        "Validator: checking subtask %d/%d (attempt %d): %.60s",
        current_idx + 1, len(checklist), attempts + 1, subtask_desc,
    )

    # Attempt vision-based validation
    validation_passed = await _try_vision_validation(
        subtask_desc, tool_results, mcp_client
    )

    if validation_passed:
        # Mark completed, advance index
        checklist[current_idx] = {
            **current_subtask,
            "status": "completed",
        }
        next_idx = current_idx + 1

        logger.info("Validator: subtask %d PASSED", current_idx + 1)

        # Check if all subtasks are done
        if next_idx >= len(checklist):
            logger.info("Validator: all %d subtasks completed!", len(checklist))
            return {
                "checklist": checklist,
                "current_subtask_index": next_idx,
                "status": "done",
            }
        else:
            # More subtasks to execute
            return {
                "checklist": checklist,
                "current_subtask_index": next_idx,
                "status": "executing",
                "error_count": 0,
            }
    else:
        # Mark failed, increment attempts
        new_attempts = attempts + 1
        checklist[current_idx] = {
            **current_subtask,
            "status": "failed" if new_attempts >= max_retries else "pending",
            "attempts": new_attempts,
        }

        if new_attempts >= max_retries:
            logger.error(
                "Validator: subtask %d FAILED after %d attempts — aborting",
                current_idx + 1, new_attempts,
            )
            return {
                "checklist": checklist,
                "status": "error",
            }
        else:
            logger.warning(
                "Validator: subtask %d FAILED (attempt %d/%d) -- retrying",
                current_idx + 1, new_attempts, max_retries,
            )
            return {
                "checklist": checklist,
                "error_count": new_attempts,
                "status": "executing",
            }


async def _try_vision_validation(
    subtask_desc: str,
    tool_results: list[dict],
    mcp_client: Any,
) -> bool:
    """
    Attempt screenshot -> Vision LLM validation.

    Falls back to tool-result-based validation if vision is unavailable.

    Screenshot strategy:
      - 'Snapshot' returns actual image content (base64 or binary)
      - 'Screenshot' returns text metadata only — not useful for vision
    """
    # Step 1: Try to take a screenshot via Windows MCP 'Snapshot' tool
    screenshot_b64 = None
    if mcp_client is not None and mcp_client.is_connected("windows"):
        try:
            raw_result = await mcp_client.call_tool_raw(
                "windows", "Snapshot", {}
            )
            # Extract base64 image data from the MCP result
            if hasattr(raw_result, "content") and raw_result.content:
                for block in raw_result.content:
                    # Binary image content block (preferred)
                    if hasattr(block, "data") and block.data:
                        # If data is bytes, encode to base64
                        if isinstance(block.data, bytes):
                            screenshot_b64 = base64.b64encode(block.data).decode("ascii")
                        else:
                            screenshot_b64 = str(block.data)
                        logger.info("Snapshot captured (%d chars of base64)", len(screenshot_b64))
                        break
                    # Some MCP servers embed base64 image in text blocks
                    elif hasattr(block, "text") and block.text:
                        text = block.text.strip()
                        # Only treat as base64 if it looks like valid base64 (long, no spaces, no JSON)
                        if (
                            len(text) > 500
                            and not text.startswith("{")
                            and not text.startswith("Cursor")
                            and " " not in text[:100]
                        ):
                            screenshot_b64 = text
                            logger.info("Snapshot text-as-base64 (%d chars)", len(text))
                            break
            if not screenshot_b64:
                logger.info("Snapshot returned no usable image data, using fallback")
        except Exception as exc:
            logger.warning("Snapshot failed (non-critical): %s", exc)

    # Step 2: Try Vision LLM if we have a valid screenshot
    if screenshot_b64 and config.OPENROUTER_API_KEY:
        try:
            vision_result = await _call_vision_llm(subtask_desc, screenshot_b64)
            if vision_result is not None:
                return vision_result
        except Exception as exc:
            logger.warning("Vision LLM validation failed (falling back): %s", exc)

    # Step 3: Fallback -- check tool results for success indicators
    return _fallback_tool_result_validation(tool_results)


async def _call_vision_llm(subtask_desc: str, screenshot_b64: str) -> bool | None:
    """
    Call the Vision LLM (Gemma 4 31B via OpenRouter) with the screenshot.

    Returns:
        True if validated, False if failed, None if unable to determine.
    """
    try:
        vision_llm = await config.get_vision_llm_rate_limited()

        # Build the multimodal message with image
        messages = [
            SystemMessage(content=_VALIDATION_PROMPT),
            HumanMessage(content=[
                {
                    "type": "text",
                    "text": f"Action attempted: {subtask_desc}\n\nDid this action succeed? Look at the screenshot below.",
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{screenshot_b64}",
                    },
                },
            ]),
        ]

        response = await vision_llm.ainvoke(messages)
        content = response.content.strip().upper()

        logger.info("Vision LLM response: %.200s", response.content.strip())

        if "RESULT: YES" in content or "RESULT:YES" in content:
            return True
        elif "RESULT: NO" in content or "RESULT:NO" in content:
            return False
        else:
            # Ambiguous — try to infer
            if "YES" in content.split("\n")[0]:
                return True
            elif "NO" in content.split("\n")[0]:
                return False
            logger.warning("Vision LLM gave ambiguous response, falling back")
            return None

    except Exception as exc:
        logger.warning("Vision LLM call failed: %s", exc)
        return None


def _fallback_tool_result_validation(tool_results: list[dict]) -> bool:
    """
    Fallback validation: check if the most recent tool results indicate success.
    """
    if not tool_results:
        return True  # No results = assume success (e.g., direct LLM response)

    # Check the most recent result(s)
    recent = tool_results[-1] if tool_results else {}
    success = recent.get("success", False)

    if success:
        logger.info("Fallback validation: tool result indicates SUCCESS")
        return True
    else:
        logger.warning(
            "Fallback validation: tool result indicates FAILURE: %s",
            recent.get("result", "unknown")[:100],
        )
        return False
