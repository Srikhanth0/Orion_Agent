"""
agents/executor.py — LangGraph executor node (V2 — Checklist-aware).

Iterates through checklist subtasks, calls the LLM with bound tools,
processes tool calls, and sets status to "validating" for the validator node.

Key difference from V1: The executor no longer advances the index —
the validator does that after confirming success.
"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import config
from agents.prompts import build_system_prompt
from agents.state import AgentState
from utils.logger import get_logger

logger = get_logger(__name__)


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

    # Build the LLM with tools bound
    llm = config.get_llm()
    if tools:
        llm_with_tools = llm.bind_tools(tools, tool_choice="any")
    else:
        llm_with_tools = llm

    # Build messages for this execution step
    exec_messages = [
        SystemMessage(content=build_system_prompt(user_id)),
    ]

    # Add memory context if available
    memory_ctx = state.get("memory_context", "")
    if memory_ctx:
        exec_messages.append(
            SystemMessage(
                content=f"Relevant past task context:\n{memory_ctx}"
            )
        )

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

    # Add conversation history (last 10 messages for context window)
    history = state.get("messages", [])[-10:]

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
                        result = await tool_map[tool_name].ainvoke(tool_args)
                        result_str = str(result)
                        # Safe logging for Windows console
                        safe_result = result_str.encode('ascii', 'replace').decode('ascii')
                        logger.info(
                            "  Tool %s returned: %.200s", tool_name, safe_result
                        )
                        step_results.append(
                            {
                                "step": current_idx + 1,
                                "tool": tool_name,
                                "args": tool_args,
                                "result": result_str,
                                "success": True,
                                "summary": f"{tool_name} completed successfully",
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
            return {
                "messages": new_messages,
                "tool_results": tool_results,
                "status": "validating",
            }

        else:
            # No tool calls — LLM responded directly
            logger.info("LLM responded without tool calls")
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
