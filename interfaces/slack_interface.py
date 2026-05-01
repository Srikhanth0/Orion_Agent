"""
interfaces/slack_interface.py — Slack async bot interface.

Listens for mentions and DMs via Socket Mode, routes them into
the LangGraph state machine, and returns the response.
"""
import asyncio
import os
from typing import Any

from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

import config
from utils.logger import get_logger

logger = get_logger(__name__)

# Module-level graph reference
_agent_graph = None


async def process_slack_event(client: SocketModeClient, req: SocketModeRequest) -> None:
    """Handle incoming Slack events."""
    if req.type == "events_api":
        # Acknowledge the request immediately to avoid Slack timeouts
        response = SocketModeResponse(envelope_id=req.envelope_id)
        await client.send_socket_mode_response(response)
        
        event = req.payload.get("event", {})
        event_type = event.get("type")
        
        # We care about DMs (message) and app mentions in channels
        if event_type in ["message", "app_mention"]:
            # Ignore messages from bots (including ourselves)
            if "bot_id" in event:
                return
                
            user_id = event.get("user")
            channel = event.get("channel")
            text = event.get("text", "")
            ts = event.get("ts")
            thread_ts = event.get("thread_ts", ts)  # Use thread if it exists
            
            # Clean up the text by removing the bot mention (e.g., <@U123456> hello)
            if event_type == "app_mention":
                # Very basic cleanup — a robust implementation would use a regex
                if ">" in text:
                    text = text.split(">", 1)[1].strip()
                    
            if not text:
                return
                
            logger.info("Slack message from %s in %s: %.50s", user_id, channel, text)
            
            try:
                from langchain_core.messages import HumanMessage
                
                # Use channel+thread as the LangGraph thread_id
                graph_thread_id = f"slack_{channel}_{thread_ts}"
                thread_config = {"configurable": {"thread_id": graph_thread_id}}
                
                # Input state
                initial_state = {
                    "messages": [HumanMessage(content=text)],
                    "user_id": f"slack_{user_id}"
                }
                
                # Run the graph
                final_state = await _agent_graph.ainvoke(initial_state, config=thread_config)
                
                # Extract response
                messages = final_state.get("messages", [])
                response_text = "I couldn't generate a response."
                
                if messages:
                    response_text = messages[-1].content
                    
                # Send the response back to Slack
                await client.web_client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=response_text
                )
                
            except Exception as exc:
                logger.error("Error processing Slack message: %s", exc)
                await client.web_client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=f"Sorry, an error occurred: {exc}"
                )


async def start_slack_bot(graph: Any) -> None:
    """Start the Slack Socket Mode client."""
    global _agent_graph
    _agent_graph = graph
    
    bot_token = config.SLACK_BOT_TOKEN
    app_token = config.SLACK_APP_TOKEN
    
    if not bot_token or not app_token:
        logger.error("SLACK_BOT_TOKEN or SLACK_APP_TOKEN is not set.")
        return

    # Initialize Socket Mode client
    client = SocketModeClient(
        app_token=app_token,
        web_client=AsyncWebClient(token=bot_token)
    )
    
    # Add event listener
    client.socket_mode_request_listeners.append(process_slack_event)
    
    # Connect
    logger.info("Starting Slack Socket Mode client...")
    await client.connect()
    
    # Keep task alive
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("Stopping Slack bot...")
        await client.close()
