# bot.py
import os
import json
import logging
from pathlib import Path
from collections import defaultdict
from typing import Dict, Any, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from dotenv import load_dotenv

load_dotenv()

# ---------------- CONFIG ----------------
TOKEN = os.getenv("TOKEN")
PRIVATE_CHANNEL = os.getenv("PRIVATE_CHANNEL_ID")  # e.g. "-1001234567890" or "@mychannel"
JSON_PATH = Path("conversation.json")

if not TOKEN:
    raise SystemExit("❌ Missing TOKEN in .env")
if not JSON_PATH.exists():
    raise SystemExit("❌ conversation.json not found next to bot.py")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

with JSON_PATH.open(encoding="utf-8") as f:
    FLOW: Dict[str, Any] = json.load(f)

# in-memory sessions: { user_id: { "node": str, "data": {...} } }
SESSIONS: Dict[int, Dict[str, Any]] = {}


# ---------------- Helpers ----------------
def make_keyboard(options):
    if not options:
        return None
    return InlineKeyboardMarkup.from_column(
        [InlineKeyboardButton(o, callback_data=o) for o in options]
    )


def safe_format(message: str, data: dict) -> str:
    safe_map = defaultdict(str, data or {})
    return message.format_map(safe_map)


def determine_req_type(current_node: Optional[str], next_node: Optional[str], choice: Optional[str]) -> Optional[str]:
    """
    Determine flow type with priority:
      1. If any node name contains 'provide' -> Submission
      2. Else if any node name contains 'request' -> Request
      3. Else if choice at start explicitly selects Provide/Request -> use that
      4. Else None
    This avoids mistakenly treating 'request_experience' (used for PROVIDE) as a Request
    because the upstream confirm/current node often contains 'provide'.
    """
    def lc(x): 
        return (x or "").lower()
    # Priority to 'provide' in current or next
    if 'provide' in lc(current_node) or 'provide' in lc(next_node):
        return "Submission"
    # Then 'request'
    if 'request' in lc(current_node) or 'request' in lc(next_node):
        return "Request"
    # Start-level explicit choice fallback
    if current_node == "start" and choice:
        if choice.lower() == "provide a service":
            return "Submission"
        if choice.lower() == "request a service":
            return "Request"
    return None


async def send_node(update: Update, context: ContextTypes.DEFAULT_TYPE, node_name: str):
    """Send or edit the node message to the user."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    node = FLOW[node_name]

    if user.id not in SESSIONS:
        SESSIONS[user.id] = {"node": "start", "data": {}}
    SESSIONS[user.id]["node"] = node_name

    text = node.get("message", "")
    data = SESSIONS[user.id]["data"]

    # Put chosen detail into 'detail' for the summary
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


# ---------------- Handlers ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    SESSIONS[user_id] = {"node": "start", "data": {}}
    await send_node(update, context, "start")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_node(update, context, "help")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id not in SESSIONS:
        SESSIONS[user_id] = {"node": "start", "data": {}}

    current_node = SESSIONS[user_id]["node"]
    node_def = FLOW.get(current_node, {})
    choice = query.data

    # Determine next node from JSON
    next_node = node_def.get("next", {}).get(choice)
    if not next_node:
        try:
            await query.edit_message_text("⚠️ This option is currently unavailable.")
        except Exception:
            logger.warning("Failed to edit message for unavailable option.")
        return

    # If user selected provide/request at the start (explicit)
    if current_node == "start" and choice in ("Provide a Service", "Request a Service"):
        SESSIONS[user_id]["data"]["req_type"] = "Submission" if choice == "Provide a Service" else "Request"

    # Capture category/service when on a services_xxx node
    if current_node.startswith("services_") and not current_node.endswith(("menu_provide", "menu_request")):
        try:
            category = node_def.get("message", "").split(":")[0].strip()
        except Exception:
            category = ""
        if category:
            SESSIONS[user_id]["data"]["category"] = category
        if choice not in ("Back to Categories", "Cancel"):
            SESSIONS[user_id]["data"]["service"] = choice

    # Use robust determination (prefer 'provide' over 'request')
    inferred = determine_req_type(current_node, next_node, choice)
    if inferred:
        SESSIONS[user_id]["data"]["req_type"] = inferred

    # Final confirmation: user pressed Confirm on the summary
    if current_node == "request_summary" and choice == "Confirm Transaction":
        d = SESSIONS[user_id]["data"]
        # Final fallback: if still missing, infer from presence of experience field
        req_type = "Submission" if d.get("experience") else "Request" # d.get("req_type") # or ("Submission" if d.get("experience") else "Request")
        details = d.get("project_details", "") or d.get("experience", "")

        # Send different messages for Request vs Submission
        if PRIVATE_CHANNEL:
            if req_type == "Request":
                header = "📩 *New SERVICE REQUEST!*"
                emoji = "📩"
            else:
                header = "🚀 *New SERVICE SUBMISSION!*"
                emoji = "🚀"

            summary_text = (
                f"{header}\n\n"
                f"👤 User: @{d.get('username','')}\n"
                f"📂 Category: {d.get('category','')}\n"
                f"🛠️ Service: {d.get('service','')}\n"
                f"📝 Details: {details}"
            )

            try:
                # use Markdown to highlight header
                await context.bot.send_message(chat_id=PRIVATE_CHANNEL, text=summary_text, parse_mode="Markdown")
            except Exception as e:
                logger.error("Failed to send to PRIVATE_CHANNEL: %s", e)

        # After sending, move to the configured next node
        next_node = node_def.get("next", {}).get(choice, "request_confirmation")

    await send_node(update, context, next_node)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if user_id not in SESSIONS:
        SESSIONS[user_id] = {"node": "start", "data": {}}
    node = SESSIONS[user_id]["node"]

    # Free-text for 'Other' service option
    if node.startswith("input_"):
        SESSIONS[user_id]["data"]["service"] = text
        # determine from node name (input_xxx_provide or input_xxx_request)
        inferred = determine_req_type(node, None, None)
        if inferred:
            SESSIONS[user_id]["data"]["req_type"] = inferred
        if node.endswith("_provide"):
            await send_node(update, context, "request_experience")
        else:
            await send_node(update, context, "request_details")
        return

    # Project description (Request)
    if node == "request_details":
        SESSIONS[user_id]["data"]["project_details"] = text
        SESSIONS[user_id]["data"]["req_type"] = "Request"
        await send_node(update, context, "request_username")
        return

    # Experience (Submission)
    if node == "request_experience":
        SESSIONS[user_id]["data"]["experience"] = text
        SESSIONS[user_id]["data"]["req_type"] = "Submission"
        await send_node(update, context, "request_username")
        return

    # Username -> summary
    if node == "request_username":
        SESSIONS[user_id]["data"]["username"] = text.lstrip("@")
        await send_node(update, context, "request_summary")
        return

    # fallback
    await update.message.reply_text("Please use the provided buttons. Use /start to restart.")


# ---------------- Main ----------------
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("🤖 Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
