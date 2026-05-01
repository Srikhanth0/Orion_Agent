"""
interfaces/cli_interface.py — Command-line async REPL interface.

Useful for testing the agent locally without messaging platforms.
"""
import sys
import asyncio
import aioconsole
from typing import Any

from langchain_core.messages import HumanMessage

from utils.logger import get_logger

logger = get_logger(__name__)

# ANSI colours for console output
_C_PROMPT = "\033[36m"     # cyan
_C_AGENT = "\033[35m"      # magenta
_C_RESET = "\033[0m"


async def start_cli(graph: Any) -> None:
    """
    Start an interactive async REPL loop in the terminal.
    """
    print(f"{_C_AGENT}AGENT ORION CLI starting. Type 'quit' or 'exit' to stop.{_C_RESET}")
    print(f"{_C_AGENT}---------------------------------------------------------{_C_RESET}\n")
    
    # Use a fixed thread ID for the CLI session
    thread_config = {"configurable": {"thread_id": "cli_session_01"}}
    
    try:
        while True:
            # Get user input asynchronously
            user_input = await aioconsole.ainput(f"{_C_PROMPT}User> {_C_RESET}")
            user_input = user_input.strip()
            
            if not user_input:
                continue
                
            if user_input.lower() in ("quit", "exit", "stop"):
                print(f"{_C_AGENT}AGENT ORION shutting down...{_C_RESET}")
                break
                
            # Input state
            initial_state = {
                "messages": [HumanMessage(content=user_input)],
                "user_id": "cli_user"
            }
            
            # Show a simple waiting indicator
            print(f"{_C_AGENT}Orion is thinking...{_C_RESET}", end="\r")
            
            try:
                # Run the graph
                final_state = await graph.ainvoke(initial_state, config=thread_config)
                
                # Clear the thinking indicator
                print(" " * 30, end="\r")
                
                # Extract response
                messages = final_state.get("messages", [])
                
                if messages:
                    response_text = messages[-1].content
                    # Ensure we can print to the console without encoding errors
                    try:
                        print(f"{_C_AGENT}Orion> {_C_RESET}{response_text}\n")
                    except UnicodeEncodeError:
                        safe_text = response_text.encode('ascii', 'replace').decode('ascii')
                        print(f"{_C_AGENT}Orion> {_C_RESET}{safe_text}\n")
                else:
                    print(f"{_C_AGENT}Orion> {_C_RESET}[No response generated]\n")
                    
            except Exception as exc:
                print(" " * 30, end="\r")
                logger.error("Graph execution failed: %s", exc)
                print(f"{_C_AGENT}Orion> {_C_RESET}Error: {exc}\n")
                
    except asyncio.CancelledError:
        logger.info("CLI task cancelled.")
