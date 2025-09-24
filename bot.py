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

# ------------------------------------------------------------
# Environment & Logging Setup
# ------------------------------------------------------------
load_dotenv()
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise ValueError("❌ BOT_TOKEN not set in .env")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------
# Load JSON Conversation
# ------------------------------------------------------------
with open("conversation.json", "r", encoding="utf-8") as f:
    CONVO = json.load(f)

# User session memory
user_sessions = {}

# ------------------------------------------------------------
# Helper Functions
# ------------------------------------------------------------
async def send_node(update: Update, context: ContextTypes.DEFAULT_TYPE, node_key: str):
    """
    Send a conversation node defined in the JSON file.
    """
    node = CONVO[node_key]
    user_id = update.effective_user.id
    user_sessions[user_id]["state"] = node_key

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

# ------------------------------------------------------------
# Command Handlers
# ------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Start the bot and initialize a new session.
    """
    user_id = update.effective_user.id
    user_sessions[user_id] = {"state": "start", "flow_type": None}
    await send_node(update, context, "start")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Display help information.
    """
    await send_node(update, context, "help")


async def my_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Retrieve a list of the user's active requests.
    """
    user_id = update.effective_user.id

    # ---------------- DATABASE PLACEHOLDER ----------------
    # rows = database.get_requests_by_user(user_id)
    rows = []  # Replace with DB call above

    if not rows:
        await update.message.reply_text("📭 You have no active requests.")
        return

    msg = "📌 Your active requests:\n\n"
    for r in rows:
        msg += f"ID: {r['id']} | {r['service']} | {r['status']}\nDetails: {r['details']}\n\n"
    await update.message.reply_text(msg)

# ------------------------------------------------------------
# Callback Query Handler
# ------------------------------------------------------------
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle all inline button presses.
    """
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    text = query.data.strip()

    session = user_sessions.get(user_id, {"state": "start", "flow_type": None})
    state = session["state"]
    node = CONVO.get(state, {})

    # Capture flow type when starting
    if state == "start":
        if text == "Request a Service":
            session["flow_type"] = "request"
        elif text == "Provide a Service":
            session["flow_type"] = "provide"
        user_sessions[user_id] = session

    # Navigation logic
    if "next" in node and text in node["next"]:
        next_state = node["next"][text]

        # -------- Request Flow -------- #
        if next_state == "request_details" and session.get("flow_type") == "request":
            # Store category/service for summary
            session["service"] = text
            user_sessions[user_id] = session
            await send_node(update, context, next_state)
            return

        elif next_state == "request_username" and session.get("flow_type") == "request":
            await send_node(update, context, next_state)
            return

        elif next_state == "request_summary" and session.get("flow_type") == "request":
            summary_node = CONVO["request_summary"]
            summary_text = summary_node["message"].format(
                category=session.get("category", ""),
                service=session.get("service", ""),
                project_details=session.get("details", ""),
                username=session.get("username", "")
            )
            keyboard = [[InlineKeyboardButton(opt, callback_data=opt)]
                        for opt in summary_node["options"]]
            await query.edit_message_text(summary_text, reply_markup=InlineKeyboardMarkup(keyboard))
            session["state"] = "request_summary"
            user_sessions[user_id] = session
            return

        elif next_state == "request_confirmation" and session.get("flow_type") == "request":
            # ---------------- DATABASE PLACEHOLDER ----------------
            # database.add_request(
            #     user_id=user_id,
            #     category=session.get("category", ""),
            #     service=session.get("service", ""),
            #     details=session.get("details", ""),
            #     username=session.get("username", "")
            # )
            await send_node(update, context, next_state)
            return

        # -------- Provide Flow -------- #
        if next_state.startswith("confirm_") and session.get("flow_type") == "provide":
            # This is where the provider confirms their service
            # ---------------- DATABASE PLACEHOLDER ----------------
            # database.add_provide(
            #     user_id=user_id,
            #     category=session.get("category", ""),
            #     service=text,
            #     username=query.from_user.username or ""
            # )
            await send_node(update, context, next_state)
            return

        # Generic navigation
        await send_node(update, context, next_state)
        return

# ------------------------------------------------------------
# Message Handler
# ------------------------------------------------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle free-text user inputs for details, username, and custom 'Other' services.
    """
    user_id = update.effective_user.id
    text = update.message.text.strip()
    session = user_sessions.get(user_id, {"state": "start", "flow_type": None})
    state = session["state"]

    # Request flow inputs
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

    # Handling free text for 'Other' services
    if state.startswith("input_") and session.get("flow_type") == "request":
        session["service"] = text
        user_sessions[user_id] = session
        await send_node(update, context, "request_details")
        return

# ------------------------------------------------------------
# Main Entrypoint
# ------------------------------------------------------------
def main():
    # ---------------- DATABASE PLACEHOLDER ----------------
    # database.init_db()

    app = Application.builder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("myrequests", my_requests))

    # Callback / Messages
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🚀 Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
