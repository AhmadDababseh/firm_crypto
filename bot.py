# bot.py
import os
import json
import logging
from pathlib import Path
from collections import defaultdict
from typing import Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------- CONFIG ----------------
TOKEN = "8337087956:AAGcFT8vxC0of0rUwqz69CzqTxuC1FqLcNQ" # prefer env var
JSON_PATH = Path("conversation.json")
if not JSON_PATH.exists():
    raise SystemExit("conversation.json not found - place it next to bot.py")
# ----------------------------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

with JSON_PATH.open(encoding="utf-8") as f:
    FLOW: Dict[str, Any] = json.load(f)

# in-memory session: { user_id: { "node": str, "data": {...} } }
SESSIONS: Dict[int, Dict[str, Any]] = {}


# ---------------- Helpers ----------------
def make_keyboard(options):
    if not options:
        return None
    return InlineKeyboardMarkup.from_column([InlineKeyboardButton(o, callback_data=o) for o in options])


def safe_format(message: str, data: dict) -> str:
    """
    Safely format the node message. Missing keys are replaced with empty string.
    """
    safe_map = defaultdict(str, data or {})
    return message.format_map(safe_map)


async def send_node(update: Update, context: ContextTypes.DEFAULT_TYPE, node_name: str):
    """
    Send or edit the message for a node.
    """
    user = update.effective_user
    chat_id = update.effective_chat.id
    node = FLOW[node_name]
    # ensure session exists
    if user.id not in SESSIONS:
        SESSIONS[user.id] = {"node": "start", "data": {}}
    SESSIONS[user.id]["node"] = node_name

    text = node.get("message", "")
    data = SESSIONS[user.id]["data"]

    # For the summary node we prefer to show either project_details or experience in 'detail'
    if node_name == "request_summary":
        # populate 'detail' from project_details or experience
        data["detail"] = data.get("project_details", "") or data.get("experience", "")

    rendered = safe_format(text, data)
    keyboard = make_keyboard(node.get("options", []))

    # If this was triggered by a callback, edit the message; otherwise send a fresh message
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text=rendered, reply_markup=keyboard)
        except Exception:
            # fallback to sending a new message if editing fails
            await context.bot.send_message(chat_id=chat_id, text=rendered, reply_markup=keyboard)
    else:
        await context.bot.send_message(chat_id=chat_id, text=rendered, reply_markup=keyboard)


# ---------------- Handlers ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    SESSIONS[user_id] = {"node": "start", "data": {}}
    await send_node(update, context, "start")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_node(update, context, "help")


async def my_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # ---------------- DATABASE PLACEHOLDER ----------------
    # rows = database.get_requests_by_user(user_id)
    rows = []  # TODO: replace with real DB call
    if not rows:
        await update.message.reply_text("📭 You have no active requests.")
        return
    text = "📌 Your active requests:\n\n"
    for r in rows:
        text += f"ID: {r.get('id')} | Service: {r.get('service')} | Status: {r.get('status')}\nDetails: {r.get('details')}\n\n"
    await update.message.reply_text(text)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    # ensure session exists
    if user_id not in SESSIONS:
        SESSIONS[user_id] = {"node": "start", "data": {}}

    current_node = SESSIONS[user_id]["node"]
    node_def = FLOW.get(current_node, {})
    choice = query.data

    # find next node defined in JSON
    next_map = node_def.get("next", {})
    next_node = next_map.get(choice)
    if not next_node:
        # unexpected - safe fallback
        await query.edit_message_text("⚠️ This option is currently unavailable.")
        return

    # If we are on a subservice-selection node, capture category & service
    # Nodes like 'services_web3_provide' are subservice-selection nodes.
    if current_node.startswith("services_") and not current_node.endswith(("menu_provide", "menu_request")):
        # set category from message header (e.g., "Web3 Services: ...")
        try:
            category = node_def.get("message", "").split(":")[0].strip()
        except Exception:
            category = ""
        if category:
            SESSIONS[user_id]["data"]["category"] = category
        if choice not in ("Back to Categories", "Cancel"):
            SESSIONS[user_id]["data"]["service"] = choice

    # move to next node
    # Special-case: when next_node is "request_summary" we ensure detail is populated (handled in send_node)
    await send_node(update, context, next_node)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if user_id not in SESSIONS:
        SESSIONS[user_id] = {"node": "start", "data": {}}
    node = SESSIONS[user_id]["node"]

    # When user types the "Other ..." service text
    if node.startswith("input_"):
        # user typed custom service text; store and route to experience or details
        SESSIONS[user_id]["data"]["service"] = text
        if node.endswith("_provide"):
            # PROVIDE flow => ask for experience
            await send_node(update, context, "request_experience")
        else:
            # REQUEST flow => ask for project details
            await send_node(update, context, "request_details")
        return

    # Request flow: project description
    if node == "request_details":
        SESSIONS[user_id]["data"]["project_details"] = text
        await send_node(update, context, "request_username")
        return

    # Provide flow: experience
    if node == "request_experience":
        SESSIONS[user_id]["data"]["experience"] = text
        await send_node(update, context, "request_username")
        return

    # username step -> summary
    if node == "request_username":
        SESSIONS[user_id]["data"]["username"] = text.lstrip("@")
        # request_summary will present detail derived from project_details or experience
        await send_node(update, context, "request_summary")
        return

    # fallback
    await update.message.reply_text("Please use the provided buttons. If you need help, /start or /help.")


# ---------------- Main ----------------
def main():
    # Optional: initialize your DB here
    # ---------------- DATABASE PLACEHOLDER ----------------
    # import database
    # database.init_db()
    # --------------------------------------------------

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("myrequests", my_requests))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
