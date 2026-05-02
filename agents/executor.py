"""
agents/executor.py — LangGraph executor node (V3 — Anti-hallucination).

Iterates through checklist subtasks, calls the LLM with bound tools,
processes tool calls, and sets status to "validating" for the validator node.

V3 additions:
  - FAILURE_PATTERNS: detects silent tool failures from result strings
  - result_evidence: first 500 chars of raw result for validator inspection
  - Structured memory context injection via build_memory_context_prompt()
"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import config
from agents.prompts import build_system_prompt, build_memory_context_prompt
from agents.state import AgentState
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Failure detection (Phase 1 — Anti-Hallucination) ──────────────────────────
FAILURE_PATTERNS = [
    "error:", "failed:", "exception:", "not found", "access denied",
    "permission denied", "cannot find", "does not exist", "traceback",
    "is not recognized", "cannot be null", "timed out", "refused",
    "connection reset", "no such",
]

# Speed fix: keywords that indicate non-GUI subtasks (skip vision validation)
_NON_GUI_TOOL_KEYWORDS = {
    "gmail", "calendar", "drive", "sheets", "market_data", "sec_filings",
    "filesystem", "powershell", "file_read", "file_write", "fintech",
    "embed_task", "log_task", "send_email", "read_inbox", "search_files",
}


def _is_non_gui_subtask(subtask: str) -> bool:
    """Return True if this subtask doesn't need visual verification."""
    low = subtask.lower()
    return any(kw in low for kw in _NON_GUI_TOOL_KEYWORDS) or low.startswith("use_api")


def _is_tool_result_failure(result_str: str) -> bool:
    """Detect silent failures returned as strings instead of exceptions."""
    lowered = result_str.lower().strip()
    return any(p in lowered for p in FAILURE_PATTERNS) or len(lowered) < 2


async def executor_node(state: AgentState, tools: list | None = None) -> dict:
    """
    Execute the current pending subtask in the checklist using the LLM with bound tools.

    The executor:
    1. Finds the first "pending" subtask at current_subtask_index
    2. Calls the 70B LLM with all available tools bound
    3. If the LLM returns tool_calls, executes them and collects results
    4. Sets status to "validating" so the validator node can verify
    5. On exception, increments error_count

    Args:
        state: The current agent state.
        tools: List of LangChain tool objects to bind to the LLM.

    Returns:
        Updated state dict with tool_results, messages, status="validating".
    """
    tools = tools or []
    checklist = state.get("checklist", [])
    current_idx = state.get("current_subtask_index", 0)
    error_count = state.get("error_count", 0)
    tool_results = list(state.get("tool_results", []))
    user_id = state.get("user_id", "")
    max_retries = config.AGENT_MAX_RETRIES

    # Check if we've completed all subtasks
    if current_idx >= len(checklist):
        logger.info("All %d checklist subtasks completed", len(checklist))
        return {"status": "done"}

    # Check retry limit
    if error_count >= max_retries:
        logger.error("Max retries (%d) reached — aborting", max_retries)
        return {"status": "error"}

    current_item = checklist[current_idx]
    current_task = current_item.get("subtask", "")
    logger.info(
        "Executing subtask %d/%d: %s", current_idx + 1, len(checklist), current_task
    )

    # Build the LLM with tools bound (use fast executor LLM with 1024 max_tokens)
    llm = config.get_executor_llm()
    attempts = current_item.get("attempts", 0)
    if tools:
        # On retries, use "required" to force a tool call
        choice = "required" if attempts > 0 else "any"
        llm_with_tools = llm.bind_tools(tools, tool_choice=choice, parallel_tool_calls=False)
    else:
        llm_with_tools = llm

    # Build messages for this execution step
    if attempts > 0:
        # RETRY MODE: stripped-down prompt to prevent drift
        last_failure = ""
        for r in reversed(tool_results):
            if not r.get("success"):
                last_failure = r.get("result", "unknown error")[:300]
                break
        exec_messages = [
            SystemMessage(content=f"""RETRY MODE — Attempt {attempts+1}/{max_retries}
YOUR ONLY TASK: {current_task}
Previous failure: {last_failure}
RULES: Call ONLY the tool for this exact subtask. Do NOT send emails or write files as error recovery.
If you cannot complete this task, call no tool."""),
            HumanMessage(content=current_task),
        ]
        history = []  # Strip history on retries to prevent drift
    else:
        exec_messages = [
            SystemMessage(content=build_system_prompt(
                user_id=user_id,
                screen_width=state.get("screen_width", 1920),
                screen_height=state.get("screen_height", 1080),
                dpi_scale=state.get("dpi_scale", 1.0),
            )),
        ]

    # Add memory context if available (structured injection)
    memory_ctx = state.get("memory_context", "")
    memory_prompt = build_memory_context_prompt(memory_ctx)
    if memory_prompt:
        exec_messages.append(SystemMessage(content=memory_prompt))

    # Add previous tool results as context
    if tool_results:
        results_summary = "\n".join(
            f"Step {r.get('step', '?')}: {r.get('summary', r.get('result', 'done'))}"
            for r in tool_results
        )
        exec_messages.append(
            SystemMessage(
                content=f"Previous step results:\n{results_summary}"
            )
        )

    # Add the current subtask as a user message
    exec_messages.append(HumanMessage(content=current_task))

    # Add conversation history (last 3 messages — reduced for speed)
    history = state.get("messages", [])[-3:]

    try:
        # Call the LLM
        logger.info("Calling LLM (ainvoke)...")
        response: AIMessage = await llm_with_tools.ainvoke(
            exec_messages + list(history)
        )

        # Check if the LLM wants to call tools
        if response.tool_calls:
            logger.info(
                "LLM requested %d tool call(s)", len(response.tool_calls)
            )
            new_messages = [response]
            step_results = []

            # Build a tool lookup map
            tool_map = {t.name: t for t in tools} if tools else {}

            for tc in response.tool_calls:
                tool_name = tc["name"]
                tool_args = tc["args"]
                logger.info("  Calling tool: %s(%s)", tool_name, tool_args)

                if tool_name in tool_map:
                    try:
                        # Defensive parsing: LLM sometimes passes "[100, 200]" string instead of list
                        import json
                        if tool_name in ["Click", "Type", "Move", "Scroll"]:
                            if "loc" in tool_args and isinstance(tool_args["loc"], str):
                                try:
                                    parsed = json.loads(tool_args["loc"])
                                    if isinstance(parsed, list):
                                        tool_args["loc"] = parsed
                                except json.JSONDecodeError:
                                    pass
                        if tool_name in ["MultiSelect", "MultiEdit"]:
                            for key in ["locs", "labels"]:
                                if key in tool_args and isinstance(tool_args[key], str):
                                    try:
                                        parsed = json.loads(tool_args[key])
                                        if isinstance(parsed, list):
                                            tool_args[key] = parsed
                                    except json.JSONDecodeError:
                                        pass
                        if tool_name == "Type" and "text" in tool_args:
                            if not isinstance(tool_args["text"], str):
                                tool_args["text"] = json.dumps(tool_args["text"])

                        result = await tool_map[tool_name].ainvoke(tool_args)
                        result_str = str(result)
                        # Safe logging for Windows console
                        safe_result = result_str.encode('ascii', 'replace').decode('ascii')
                        logger.info(
                            "  Tool %s returned: %.200s", tool_name, safe_result
                        )
                        success = not _is_tool_result_failure(result_str)
                        step_results.append(
                            {
                                "step": current_idx + 1,
                                "tool": tool_name,
                                "args": tool_args,
                                "result": result_str,
                                "result_evidence": result_str[:500],
                                "success": success,
                                "summary": (
                                    f"{tool_name} completed successfully"
                                    if success
                                    else f"{tool_name} FAILED: {result_str[:150]}"
                                ),
                            }
                        )
                        new_messages.append(
                            ToolMessage(
                                content=result_str,
                                tool_call_id=tc["id"],
                            )
                        )
                    except Exception as exc:
                        error_msg = f"Tool {tool_name} failed: {exc}"
                        logger.error("  %s", error_msg)
                        step_results.append(
                            {
                                "step": current_idx + 1,
                                "tool": tool_name,
                                "args": tool_args,
                                "result": error_msg,
                                "success": False,
                                "summary": error_msg,
                            }
                        )
                        new_messages.append(
                            ToolMessage(
                                content=error_msg,
                                tool_call_id=tc["id"],
                            )
                        )
                else:
                    error_msg = f"Unknown tool: {tool_name}"
                    logger.warning("  %s", error_msg)
                    step_results.append(
                        {
                            "step": current_idx + 1,
                            "tool": tool_name,
                            "args": tool_args,
                            "result": error_msg,
                            "success": False,
                            "summary": error_msg,
                        }
                    )
                    new_messages.append(
                        ToolMessage(
                            content=error_msg,
                            tool_call_id=tc["id"],
                        )
                    )

            tool_results.extend(step_results)

            # Route to validator for verification (not directly advancing)
            is_non_gui = _is_non_gui_subtask(current_task)
            return {
                "messages": new_messages,
                "tool_results": tool_results,
                "status": "validating",
                "skip_vision": is_non_gui,
            }

        else:
            # No tool calls — LLM responded directly
            logger.info("LLM responded without tool calls")

            # Fix 1.4: Detect tool-required subtasks that produced no tool call
            TOOL_REQUIRED_KEYWORDS = {
                "use_api", "run_powershell", "click", "snapshot", "launch",
                "type", "shortcut", "read_file", "write_file", "send_email",
                "scrape", "filesystem", "market_data", "verify", "powershell",
                "find", "click_at", "type_text", "press_key",
            }
            requires_tool = any(kw in current_task.lower() for kw in TOOL_REQUIRED_KEYWORDS)
            if requires_tool:
                logger.warning("Tool-required subtask got no tool call — SOFT_FAIL")
                tool_results.append({
                    "step": current_idx + 1,
                    "tool": "llm_direct",
                    "args": {},
                    "result": "SOFT_FAIL: No tool called for tool-required subtask",
                    "result_evidence": "SOFT_FAIL: no tool was called",
                    "success": False,
                    "summary": "Required tool call was skipped",
                })
                return {
                    "messages": [response],
                    "tool_results": tool_results,
                    "status": "validating",
                }

            tool_results.append(
                {
                    "step": current_idx + 1,
                    "tool": "llm_direct",
                    "args": {},
                    "result": response.content,
                    "success": True,
                    "summary": response.content[:200],
                }
            )
            # Still route to validator
            return {
                "messages": [response],
                "tool_results": tool_results,
                "status": "validating",
            }

    except Exception as exc:
        logger.error("Executor failed on subtask %d: %s", current_idx + 1, exc)
        return {
            "error_count": error_count + 1,
            "tool_results": tool_results + [
                {
                    "step": current_idx + 1,
                    "tool": "executor",
                    "args": {},
                    "result": f"Execution error: {exc}",
                    "success": False,
                    "summary": f"Execution error: {exc}",
                }
            ],
            "status": "validating",
        }
