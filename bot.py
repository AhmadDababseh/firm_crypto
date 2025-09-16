# bot.py
import os
import json
import logging
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# Local imports
import database

# Load environment
load_dotenv()
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise ValueError("❌ BOT_TOKEN not set in .env")

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load JSON conversation file
with open("conversation.json", "r", encoding="utf-8") as f:
    CONVO = json.load(f)

# Session memory
user_sessions = {}

# ------------------- Helper Functions ------------------- #
async def send_node(update: Update, context: ContextTypes.DEFAULT_TYPE, node_key: str):
    """Send a node from JSON conversation"""
    node = CONVO[node_key]
    user_sessions[update.effective_user.id]["state"] = node_key

    text = node.get("message") or node.get("description") or "..."

    options = node.get("options", [])
    reply_markup = None
    if options:
        keyboard = [[InlineKeyboardButton(opt, callback_data=opt)] for opt in options]
        reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup)

# ------------------- Handlers ------------------- #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the bot"""
    state = "start"
    user_sessions[update.effective_user.id] = {"state": state, "flow_type": None}
    await send_node(update, context, state)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button presses"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    text = query.data.strip()
    session = user_sessions.get(user_id, {"state": "start", "flow_type": None})
    state = session["state"]

    node = CONVO.get(state, {})

    # Track flow type at start
    if state == "start":
        if text == "Request a Service":
            session["flow_type"] = "request"
        elif text == "Provide a Service":
            session["flow_type"] = "provide"
        user_sessions[user_id] = session

    if "next" in node and text in node["next"]:
        next_state = node["next"][text]

        # ---------------- Request Flow ---------------- #
        if next_state == "request_details" and session.get("flow_type") == "request":
            session["project_type"] = text
            user_sessions[user_id] = session
            await send_node(update, context, "request_details")
            return

        elif next_state == "request_username" and session.get("flow_type") == "request":
            await send_node(update, context, "request_username")
            return

        elif next_state == "request_summary" and session.get("flow_type") == "request":
            summary_node = CONVO["request_summary"]
            text_summary = summary_node["message"].format(
                project_type=session.get("project_type", ""),
                project_details=session.get("details", ""),
                username=session.get("username", "")
            )
            keyboard = [[InlineKeyboardButton(opt, callback_data=opt)] for opt in summary_node["options"]]
            await query.edit_message_text(text_summary, reply_markup=InlineKeyboardMarkup(keyboard))
            session["state"] = "request_summary"
            user_sessions[user_id] = session
            return

        elif next_state == "request_confirmation" and session.get("flow_type") == "request":
            # Save request to DB
            database.add_request(
                user_id,
                session.get("project_type", ""),
                session.get("details", ""),
                session.get("username", "")
            )
            await send_node(update, context, "request_confirmation")
            return

        # ---------------- Provide Flow ---------------- #
        if next_state.startswith("services_") and "description" in CONVO[next_state]:
            if session.get("flow_type") == "provide":
                service_category = state.replace("services_", "")
                subservice = text
                description = CONVO[next_state]["description"]

                database.add_provide(
                    user_id=user_id,
                    service_category=service_category,
                    subservice=subservice,
                    description=description,
                    username=query.from_user.username or ""
                )
                await query.edit_message_text(f"✅ Your service has been saved:\n\n{subservice}\n{description}")
                return

        # Generic navigation
        await send_node(update, context, next_state)
        return

# ------------------- Free Text Handlers ------------------- #
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free text inputs for request details & username"""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    session = user_sessions.get(user_id, {"state": "start", "flow_type": None})
    state = session["state"]

    if state == "request_details" and session.get("flow_type") == "request":
        session["details"] = text
        user_sessions[user_id] = session
        await send_node(update, context, "request_username")
        return

    if state == "request_username" and session.get("flow_type") == "request":
        session["username"] = text
        user_sessions[user_id] = session
        await send_node(update, context, "request_summary")
        return

# ------------------- Command Handlers ------------------- #
async def my_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's active requests"""
    user_id = update.effective_user.id
    rows = database.get_requests_by_user(user_id)

    if not rows:
        await update.message.reply_text("📭 You have no active requests.")
        return

    msg = "📌 Your active requests:\n\n"
    for r in rows:
        msg += f"ID: {r['id']} | {r['project_type']} | {r['status']}\nDetails: {r['details']}\n\n"

    await update.message.reply_text(msg)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help"""
    await send_node(update, context, "help")

# ------------------- Main ------------------- #
def main():
    database.init_db()
    app = Application.builder().token(TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("myrequests", my_requests))

    # Message & callback handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("🚀 Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
