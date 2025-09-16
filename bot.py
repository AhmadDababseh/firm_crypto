# bot.py
import os
import json
import logging
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
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

# States
CHOOSING, REQUEST_DETAILS, REQUEST_USERNAME, REQUEST_CONFIRM = range(4)

# Session memory
user_sessions = {}

# -------- Handlers -------- #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the bot"""
    state = "start"
    user_sessions[update.effective_user.id] = {"state": state}
    await send_node(update, context, state)


async def send_node(update: Update, context: ContextTypes.DEFAULT_TYPE, node_key: str):
    """Send a node from JSON conversation"""
    node = CONVO[node_key]
    user_sessions[update.effective_user.id]["state"] = node_key

    if "message" in node:
        text = node["message"]
    elif "description" in node:
        text = node["description"]
    else:
        text = "..."

    options = node.get("options", [])
    reply_markup = ReplyKeyboardMarkup(
        [options[i:i + 2] for i in range(0, len(options), 2)],
        resize_keyboard=True,
        one_time_keyboard=True
    ) if options else None

    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text responses"""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    session = user_sessions.get(user_id, {"state": "start"})
    state = session["state"]

    # If state has "next", move forward
    node = CONVO.get(state, {})
    if "next" in node and text in node["next"]:
        next_state = node["next"][text]

        # Handle request flow
        if next_state == "request_details":
            session["project_type"] = text
            user_sessions[user_id] = session
            await send_node(update, context, "request_details")
            return

        elif next_state == "request_username":
            await send_node(update, context, "request_username")
            return

        elif next_state == "request_summary":
            summary_node = CONVO["request_summary"]
            text_summary = summary_node["message"].format(
                project_type=session.get("project_type", ""),
                project_details=session.get("details", ""),
                username=session.get("username", "")
            )
            await update.message.reply_text(
                text_summary,
                reply_markup=ReplyKeyboardMarkup(
                    [summary_node["options"]],
                    resize_keyboard=True,
                    one_time_keyboard=True
                )
            )
            session["state"] = "request_summary"
            user_sessions[user_id] = session
            return

        elif next_state == "request_confirmation":
            # Save to DB
            database.add_request(
                user_id,
                session.get("project_type", ""),
                session.get("details", ""),
                session.get("username", "")
            )
            await send_node(update, context, "request_confirmation")
            return

        # Handle provide service flow
        if next_state.startswith("services_") and "description" in CONVO[next_state]:
            service_category = state.replace("services_", "")
            subservice = text
            description = CONVO[next_state]["description"]

            database.add_provide(
                user_id=user_id,
                service_category=service_category,
                subservice=subservice,
                description=description,
                username=update.effective_user.username or ""
            )
            await update.message.reply_text(
                f"✅ Your service has been saved:\n\n{subservice}\n{description}"
            )
            return

        # Generic navigation
        await send_node(update, context, next_state)
        return

    # Handle free text input inside request flow
    if state == "request_details":
        session["details"] = text
        user_sessions[user_id] = session
        await send_node(update, context, "request_username")
        return

    if state == "request_username":
        session["username"] = text
        user_sessions[user_id] = session
        await send_node(update, context, "request_summary")
        return


async def my_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user requests"""
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


# -------- Main -------- #
def main():
    database.init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("myrequests", my_requests))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🚀 Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
