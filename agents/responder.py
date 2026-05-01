"""
agents/responder.py — LangGraph responder node.

Formats the final output from tool_results into a concise,
human-readable response.  Handles message truncation for
Telegram's 4096-char limit.
"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import config
from agents.state import AgentState
from utils.logger import get_logger

logger = get_logger(__name__)

# Telegram has a 4096-char limit for messages
MAX_RESPONSE_LENGTH = 4000


async def responder_node(state: AgentState) -> dict:
    """
    Produce the final user-facing response.

    Gathers all tool_results, sends them to the LLM to synthesize a
    clean summary, and returns it as an AIMessage.

    Returns:
        {"messages": [AIMessage(content=final_response)], "status": "done"}
    """
    tool_results = state.get("tool_results", [])
    task = state.get("task", "")
    status = state.get("status", "done")

    # If there was an error, include that context
    if status == "error":
        error_results = [r for r in tool_results if not r.get("success")]
        if error_results:
            last_error = error_results[-1]
            response = (
                f"I wasn't able to complete your request. "
                f"The last error was: {last_error.get('result', 'Unknown error')}. "
                f"Please try rephrasing or breaking the task into smaller steps."
            )
            return {
                "messages": [AIMessage(content=response)],
                "status": "done",
            }

    intent = state.get("intent", "simple_task")

    # Handle direct chat intent (no tool execution)
    if intent == "chat":
        llm = config.get_llm()
        chat_messages = [
            SystemMessage(content="You are ORION, a helpful Windows Personal Assistant. Respond conversationally to the user."),
            HumanMessage(content=task)
        ]
        response = await llm.ainvoke(chat_messages)
        return {
            "messages": [AIMessage(content=_truncate(response.content))],
            "status": "done",
        }

    # If no tool results and not chat, respond that nothing was done
    if not tool_results:
        return {
            "messages": [
                AIMessage(content="I processed your request but there were no results to report.")
            ],
            "status": "done",
        }

    # For single-step direct LLM responses, just return the content
    if (
        len(tool_results) == 1
        and tool_results[0].get("tool") == "llm_direct"
        and tool_results[0].get("success")
    ):
        content = tool_results[0]["result"]
        return {
            "messages": [AIMessage(content=_truncate(content))],
            "status": "done",
        }

    # For multi-step results, synthesize a summary via the LLM
    try:
        llm = config.get_llm()

        results_text = "\n".join(
            f"Step {r.get('step', '?')} [{r.get('tool', 'unknown')}]: "
            f"{'✓' if r.get('success') else '✗'} {r.get('result', '')[:500]}"
            for r in tool_results
        )

        synthesis_messages = [
            SystemMessage(
                content=(
                    "Summarise the following task execution results for the user. "
                    "Be concise and highlight the key outcomes. "
                    "If any steps failed, mention what went wrong. "
                    "Do NOT include raw JSON or tool names — translate everything "
                    "to plain language. Keep the response under 2000 characters."
                )
            ),
            HumanMessage(
                content=f"Original task: {task}\n\nExecution results:\n{results_text}"
            ),
        ]

        response = await llm.ainvoke(synthesis_messages)
        content = _truncate(response.content)

        logger.info("Responder synthesised %d-char response", len(content))
        return {
            "messages": [AIMessage(content=content)],
            "status": "done",
        }

    except Exception as exc:
        logger.error("Responder synthesis failed: %s", exc)
        # Fallback: just concatenate results
        fallback = "\n".join(
            f"• {r.get('summary', r.get('result', 'done'))}"
            for r in tool_results
            if r.get("success")
        )
        return {
            "messages": [AIMessage(content=_truncate(fallback or "Task completed."))],
            "status": "done",
        }


def _truncate(text: str) -> str:
    """Truncate text to fit Telegram's message limit."""
    if len(text) <= MAX_RESPONSE_LENGTH:
        return text
    return text[: MAX_RESPONSE_LENGTH - 20] + "\n\n…(truncated)"
