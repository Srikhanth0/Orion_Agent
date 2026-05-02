"""
interfaces/telegram_interface.py — Telegram async bot interface.

Listens for messages, routes them into the LangGraph state machine,
and returns the final response back to the user.
"""
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

import config
from utils.logger import get_logger

logger = get_logger(__name__)

# This will hold the compiled LangGraph object
_agent_graph = None


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command."""
    user_id = update.effective_user.id
    
    if config.ALLOWED_USER_IDS and user_id not in config.ALLOWED_USER_IDS:
        await update.message.reply_text("Unauthorized access.")
        return
        
    await update.message.reply_text(
        "Hello! I am AGENT ORION, your Windows Personal Assistant.\n"
        "I can help you control your PC, manage emails/calendar, and check financial data.\n"
        "How can I help you today?"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages and route to LangGraph."""
    if not update.message or not update.message.text:
        return
        
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = update.message.text
    
    if config.ALLOWED_USER_IDS and user_id not in config.ALLOWED_USER_IDS:
        logger.warning("Unauthorized message attempt from user %s", user_id)
        return

    logger.info("Telegram message from %s: %.50s", user_id, text)
    
    # Send a typing indicator
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    try:
        from langchain_core.messages import HumanMessage
        
        # We use the chat_id as the LangGraph thread_id for state persistence
        thread_config = {
            "configurable": {"thread_id": str(chat_id)},
            "recursion_limit": 100,
        }
        
        # Prepare the state input
        initial_state = {
            "messages": [HumanMessage(content=text)],
            "user_id": str(user_id)
        }
        
        # Run the graph
        logger.debug("Invoking graph for chat %s", chat_id)
        final_state = await _agent_graph.ainvoke(initial_state, config=thread_config)
        
        # Extract response from the responder node (last AIMessage)
        messages = final_state.get("messages", [])
        response_text = "I couldn't generate a response."
        
        if messages:
            # The responder node ensures the last message is the synthesized response
            response_text = messages[-1].content
            
        # Telegram max length is 4096 characters
        if len(response_text) > 4000:
            # Split into chunks if necessary
            chunks = [response_text[i:i+4000] for i in range(0, len(response_text), 4000)]
            for chunk in chunks:
                await update.message.reply_text(chunk)
        else:
            await update.message.reply_text(response_text)
            
    except Exception as exc:
        logger.error("Error processing Telegram message: %s", exc)
        await update.message.reply_text(f"Sorry, an error occurred: {exc}")


async def start_telegram_bot(graph) -> None:
    """Start the Telegram polling loop."""
    global _agent_graph
    _agent_graph = graph
    
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is not set.")
        return

    # Build application
    application = Application.builder().token(token).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run the bot
    logger.info("Starting Telegram bot polling...")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    # Keep the task alive but allow graceful shutdown via main.py
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("Stopping Telegram bot...")
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
