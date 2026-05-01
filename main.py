"""
main.py — Main entry point and async event loop manager.

Launches ALL THREE MCP clients (Windows, Playwright, Fincept),
compiles the LangGraph agent, and starts the requested interface
(CLI, Telegram, or Slack). Handles graceful shutdown on Ctrl+C.
"""
import argparse
import asyncio
import signal
import sys
from typing import Any

import config
from agents.graph import build_graph
from tools.mcp_client import multi_mcp_client
from tools.google_tools import GOOGLE_TOOLS
from tools.fintech_tools import FINTECH_TOOLS
from tools.fincept_tools import launch_fincept_terminal
from tools.file_tools import FILE_TOOLS
from utils.logger import get_logger

logger = get_logger(__name__)


async def main(interface: str) -> None:
    """Main orchestration coroutine."""
    logger.info("Starting AGENT ORION V2 - Windows Personal Assistant")
    logger.info("Architecture: Multi-MCP + Checklist + Vision Validator")

    # 1. Start ALL MCP Clients concurrently & discover tools
    try:
        mcp_tools = await multi_mcp_client.initialize_all()
        connected = multi_mcp_client.get_server_names()
        logger.info(
            "MCP servers connected: %s (%d total MCP tools)",
            connected, len(mcp_tools),
        )
    except Exception as exc:
        logger.error("Failed to initialize MCP clients: %s", exc)
        logger.error("Ensure windows-mcp is installed and accessible.")
        sys.exit(1)

    # Combine all tools
    all_tools = mcp_tools + GOOGLE_TOOLS + FINTECH_TOOLS + FILE_TOOLS + [launch_fincept_terminal]
    logger.info("Total tools available to agent: %d", len(all_tools))

    # 2. Build the LangGraph StateMachine (with validator + MCP client)
    try:
        agent_graph = build_graph(tools=all_tools, mcp_client=multi_mcp_client)
    except Exception as exc:
        logger.error("Failed to build LangGraph: %s", exc)
        await multi_mcp_client.shutdown_all()
        sys.exit(1)

    # 3. Launch the selected interface
    tasks = []

    try:
        if interface == "telegram":
            from interfaces.telegram_interface import start_telegram_bot
            tasks.append(asyncio.create_task(start_telegram_bot(agent_graph)))
        elif interface == "slack":
            from interfaces.slack_interface import start_slack_bot
            tasks.append(asyncio.create_task(start_slack_bot(agent_graph)))
        elif interface == "cli":
            from interfaces.cli_interface import start_cli
            tasks.append(asyncio.create_task(start_cli(agent_graph)))
        else:
            logger.error("Unknown interface: %s", interface)
            await multi_mcp_client.shutdown_all()
            sys.exit(1)

        # Wait for interface tasks (they run indefinitely until cancelled)
        await asyncio.gather(*tasks)

    except asyncio.CancelledError:
        logger.info("Main shutdown sequence initiated...")
    finally:
        # 4. Graceful shutdown — cancel tasks and kill all MCP subprocesses
        for task in tasks:
            task.cancel()
        await multi_mcp_client.shutdown_all()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AGENT ORION V2 Launcher")
    parser.add_argument(
        "--interface",
        type=str,
        choices=["telegram", "slack", "cli"],
        default="cli",
        help="The messaging interface to launch (default: cli)",
    )
    args = parser.parse_args()

    # Set up event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    main_task = loop.create_task(main(args.interface))

    # Handle Ctrl+C (SIGINT) gracefully
    def handle_sigint():
        logger.info("Received SIGINT (Ctrl+C), cancelling main task...")
        main_task.cancel()

    try:
        # In Windows, add_signal_handler only works for SIGINT/SIGBREAK
        loop.add_signal_handler(signal.SIGINT, handle_sigint)
    except NotImplementedError:
        # Fallback for platforms where add_signal_handler isn't implemented
        signal.signal(signal.SIGINT, lambda sig, frame: handle_sigint())

    try:
        loop.run_until_complete(main_task)
    except asyncio.CancelledError:
        pass
    finally:
        loop.close()
