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

import database
from dotenv import load_dotenv
load_dotenv()

# -------------------------------------------------------
# CONFIG
# -------------------------------------------------------
TOKEN = os.getenv("TOKEN")
PRIVATE_CHANNEL = os.getenv("PRIVATE_CHANNEL_ID")  # e.g. "-1001234567890"
JSON_PATH = Path("conversation.json")
if not TOKEN:
    raise SystemExit("❌ Missing TOKEN in .env")
if not JSON_PATH.exists():
    raise SystemExit("❌ conversation.json not found next to bot.py")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

with JSON_PATH.open(encoding="utf-8") as f:
    FLOW: Dict[str, Any] = json.load(f)

# In-memory session: { user_id: { "node": str, "data": {...} } }
SESSIONS: Dict[int, Dict[str, Any]] = {}


# -------------------------------------------------------
# Helpers
# -------------------------------------------------------
def make_keyboard(options):
    if not options:
        return None
    return InlineKeyboardMarkup.from_column(
        [InlineKeyboardButton(o, callback_data=o) for o in options]
    )


def safe_format(message: str, data: dict) -> str:
    safe_map = defaultdict(str, data or {})
    return message.format_map(safe_map)


async def send_node(update: Update, context: ContextTypes.DEFAULT_TYPE, node_name: str):
    """
    Send or edit the message for a node.
    """
    user = update.effective_user
    chat_id = update.effective_chat.id
    node = FLOW[node_name]

    if user.id not in SESSIONS:
        SESSIONS[user.id] = {"node": "start", "data": {}}
    SESSIONS[user.id]["node"] = node_name

    text = node.get("message", "")
    data = SESSIONS[user.id]["data"]

    if node_name == "request_summary":
        data["detail"] = data.get("project_details", "") or data.get("experience", "")

    rendered = safe_format(text, data)
    keyboard = make_keyboard(node.get("options", []))

    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text=rendered, reply_markup=keyboard)
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text=rendered, reply_markup=keyboard)
    else:
        await context.bot.send_message(chat_id=chat_id, text=rendered, reply_markup=keyboard)


# -------------------------------------------------------
# Handlers
# -------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    SESSIONS[user_id] = {"node": "start", "data": {}}
    await send_node(update, context, "start")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_node(update, context, "help")


async def my_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Show all stored requests for the user.
    """
    user_id = update.effective_user.id
    rows = database.get_requests_by_user(user_id)
    if not rows:
        await update.message.reply_text("📭 You have no active requests.")
        return
    text = "📌 Your active requests:\n\n"
    for r in rows:
        text += (
            f"ID: {r['id']} | Category: {r['category']} | Service: {r['service']} "
            f"| Status: {r['status']}\nDetails: {r['details']}\n\n"
        )
    await update.message.reply_text(text)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id not in SESSIONS:
        SESSIONS[user_id] = {"node": "start", "data": {}}

    current_node = SESSIONS[user_id]["node"]
    node_def = FLOW.get(current_node, {})
    choice = query.data
    next_node = node_def.get("next", {}).get(choice)

    if not next_node:
        await query.edit_message_text("⚠️ This option is currently unavailable.")
        return

    # Capture category & service
    if current_node.startswith("services_") and not current_node.endswith(("menu_provide", "menu_request")):
        try:
            category = node_def.get("message", "").split(":")[0].strip()
        except Exception:
            category = ""
        if category:
            SESSIONS[user_id]["data"]["category"] = category
        if choice not in ("Back to Categories", "Cancel"):
            SESSIONS[user_id]["data"]["service"] = choice

    # Final confirmation → store in DB + send to channel
    if current_node == "request_summary" and choice == "Confirm Transaction":
        d = SESSIONS[user_id]["data"]

        # ✅ Save to database
        database.add_request(
            user_id=user_id,
            username=d.get("username", ""),
            category=d.get("category", ""),
            service=d.get("service", ""),
            details=d.get("project_details", "") or d.get("experience", "")
        )

        # ✅ Notify private channel
        if PRIVATE_CHANNEL:
            summary_text = (
                f"📩 *New Request Received!*\n"
                f"👤 User: @{d.get('username','')}\n"
                f"📂 Category: {d.get('category','')}\n"
                f"🛠 Service: {d.get('service','')}\n"
                f"📝 Details: {d.get('project_details','') or d.get('experience','')}"
            )
            try:
                await context.bot.send_message(chat_id=PRIVATE_CHANNEL, text=summary_text, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Failed to send to private channel: {e}")

        next_node = node_def.get("next", {}).get(choice, "request_confirmation")

    await send_node(update, context, next_node)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_id not in SESSIONS:
        SESSIONS[user_id] = {"node": "start", "data": {}}
    node = SESSIONS[user_id]["node"]

    if node.startswith("input_"):
        SESSIONS[user_id]["data"]["service"] = text
        if node.endswith("_provide"):
            await send_node(update, context, "request_experience")
        else:
            await send_node(update, context, "request_details")
        return

    if node == "request_details":
        SESSIONS[user_id]["data"]["project_details"] = text
        await send_node(update, context, "request_username")
        return

    if node == "request_experience":
        SESSIONS[user_id]["data"]["experience"] = text
        await send_node(update, context, "request_username")
        return

    if node == "request_username":
        SESSIONS[user_id]["data"]["username"] = text.lstrip("@")
        await send_node(update, context, "request_summary")
        return

    await update.message.reply_text(
        "Please use the provided buttons. If you need help, /start or /help."
    )


# -------------------------------------------------------
# Main
# -------------------------------------------------------
def main():
    database.init_db()  # Ensure tables exist

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("myrequests", my_requests))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("🤖 Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
