"""
tools/input_tools.py — Native Windows input tools via Windows MCP.
Replaces pyautogui entirely. All mouse/keyboard ops go through Windows MCP.

CRITICAL USAGE NOTES:
  Type tool label MUST be an integer from Snapshot UI tree.
  Use Click-then-Type workflow to avoid label issues.
  Screenshot is fast; Snapshot is slow but gives element IDs.
"""
import re

from langchain_core.tools import tool
from tools.mcp_client import multi_mcp_client
from utils.logger import get_logger

logger = get_logger(__name__)


@tool
async def click_and_type(x: int, y: int, text: str, clear_first: bool = False) -> str:
    """
    Click a coordinate to focus it, then type text. No label integer needed.
    Use this instead of Type(label='string') which causes int_parsing errors.
    Args:
        x: Screen X coordinate to click (focus the input field).
        y: Screen Y coordinate to click (focus the input field).
        text: The text to type after clicking.
        clear_first: If True, select all and delete before typing.
    Returns: Confirmation string.
    """
    try:
        # Step 1: Click to focus
        click_result = await multi_mcp_client.call_tool(
            "windows", "Click", {"x": x, "y": y}
        )
        logger.info("click_and_type: clicked (%d, %d): %s", x, y, str(click_result)[:80])

        # Step 2: Optionally clear
        if clear_first:
            await multi_mcp_client.call_tool(
                "windows", "Shortcut", {"keys": "ctrl+a"}
            )
            await multi_mcp_client.call_tool(
                "windows", "Shortcut", {"keys": "delete"}
            )

        # Step 3: Type text (no label = uses focused element)
        type_result = await multi_mcp_client.call_tool(
            "windows", "Type", {"text": text}
        )
        logger.info("click_and_type: typed text successfully")
        return f"Clicked ({x},{y}) and typed: '{text[:50]}' — {type_result}"

    except Exception as exc:
        logger.error("click_and_type failed: %s", exc)
        return f"click_and_type failed: {exc}"


@tool
async def type_by_label(label: int, text: str, clear_first: bool = False) -> str:
    """
    Type text into a Windows UI element by its integer label ID.
    Get the label integer from Snapshot(use_ui_tree=True) — look for [N] in output.
    Args:
        label: Integer element ID from Snapshot UI tree output (e.g. 42, not 'search bar').
        text: Text to type into the element.
        clear_first: Clear existing text first.
    Returns: Confirmation string.
    """
    try:
        args = {"label": label, "text": text}
        if clear_first:
            args["clear"] = True
        result = await multi_mcp_client.call_tool("windows", "Type", args)
        return f"Typed into element {label}: '{text[:50]}' — {result}"
    except Exception as exc:
        return f"type_by_label failed (label={label}): {exc}"


@tool
async def fast_screenshot() -> str:
    """
    Take a fast screenshot for visual verification.
    Use this (not Snapshot) when you just need to see the current screen state.
    Screenshot is 5-10x faster than Snapshot because it skips UI tree extraction.
    Returns: Screenshot metadata and image data.
    """
    try:
        result = await multi_mcp_client.call_tool("windows", "Screenshot", {})
        return result
    except Exception as exc:
        return f"Screenshot failed: {exc}"


@tool
async def get_ui_element_id(description: str) -> str:
    """
    Take a Snapshot with UI tree and find the integer label ID of an element.
    Use this when you need to call Type with a specific element label.
    Args:
        description: Text to search for in the UI tree (e.g. 'Search', 'Address bar', 'OK').
    Returns: The integer label ID if found, or the full UI tree text if not found.
    """
    try:
        result = await multi_mcp_client.call_tool(
            "windows", "Snapshot", {"use_ui_tree": True}
        )
        # Parse element IDs from UI tree output
        # Format: "[42] Edit 'Search' ..." → label=42
        lines = result.split("\n") if isinstance(result, str) else [str(result)]
        matches = []
        for line in lines:
            if description.lower() in line.lower():
                # Extract [N] integer at start of line
                m = re.match(r"\[(\d+)\]", line.strip())
                if m:
                    matches.append((int(m.group(1)), line.strip()[:100]))

        if matches:
            best = matches[0]
            return f"Found element: label={best[0]}, description='{best[1]}'"
        else:
            # Return truncated UI tree for manual inspection
            tree_preview = "\n".join(lines[:30])
            return f"Element '{description}' not found. UI tree preview:\n{tree_preview}"

    except Exception as exc:
        return f"get_ui_element_id failed: {exc}"


@tool
async def press_shortcut(keys: str) -> str:
    """
    Press a keyboard shortcut using Windows MCP Shortcut tool.
    Args:
        keys: Key combination string. Examples: 'ctrl+c', 'ctrl+v', 'alt+tab',
              'ctrl+shift+n', 'enter', 'escape', 'win+d', 'f5'.
    Returns: Confirmation string.
    """
    try:
        result = await multi_mcp_client.call_tool("windows", "Shortcut", {"keys": keys})
        return f"Shortcut '{keys}' executed: {result}"
    except Exception as exc:
        return f"Shortcut failed: {exc}"


INPUT_TOOLS = [
    click_and_type,
    type_by_label,
    fast_screenshot,
    get_ui_element_id,
    press_shortcut,
]
