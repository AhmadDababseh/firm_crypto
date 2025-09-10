import json
import logging
import os
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# ---------- CONFIG ----------
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# MySQL credentials from .env
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "firm_db")

CONVO_FILE = "conversation.json"
# ----------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

with open(CONVO_FILE, "r", encoding="utf-8") as f:
    CONVO = json.load(f)

# ---------- DATABASE ----------
def get_connection():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME
    )

def init_db():
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD
        )
        cur = conn.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME}")
        conn.database = DB_NAME
        cur.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id BIGINT,
                username VARCHAR(255),
                project_type VARCHAR(255),
                details TEXT,
                status VARCHAR(50) DEFAULT 'Pending'
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Error as e:
        logger.error(f"MySQL init error: {e}")

def save_request(user_id, username, project_type, details):
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO requests (user_id, username, project_type, details) VALUES (%s, %s, %s, %s)",
            (user_id, username, project_type, details),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Error as e:
        logger.error(f"MySQL insert error: {e}")

def get_user_requests(user_id):
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, project_type, status FROM requests WHERE user_id = %s", (user_id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return rows
    except Error as e:
        logger.error(f"MySQL fetch error: {e}")
        return []
# --------------------------------

# ---------- HANDLERS ----------
async def goto_state(update, context, state_name: str, new_message: bool = True):
    state = CONVO.get(state_name)
    if not state:
        await update.message.reply_text("⚠️ Unknown state. Returning to main menu.")
        state_name = "start"
        state = CONVO[state_name]

    context.user_data["state_name"] = state_name
    text = state.get("message", "")

    # Dynamic summary
    if state_name == "request_summary":
        text = text.format(
            project_type=context.user_data.get("project_type", "Unknown"),
            project_details=context.user_data.get("project_details", ""),
            username=context.user_data.get("username", "")
        )

    # User requests
    if state_name == "my_requests":
        requests = get_user_requests(update.effective_user.id)
        if requests:
            text += "\n\n" + "\n".join([f"#{r[0]} | {r[1]} | {r[2]}" for r in requests])
        else:
            text += "\n\n(No requests yet.)"

    # Subservice description
    if "description" in state:
        text = state["description"]
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("Request Service", callback_data="Request Service")],
            [InlineKeyboardButton("Back", callback_data="Back")]
        ])
    else:
        # Normal buttons from JSON
        buttons = [[InlineKeyboardButton(opt, callback_data=opt)] for opt in state.get("options", [])]
        reply_markup = InlineKeyboardMarkup(buttons) if buttons else None

    # Send or edit message
    if new_message:
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
        elif update.message:
            await update.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)

    return state_name

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data
    state_name = context.user_data.get("state_name", "start")
    state = CONVO.get(state_name, {})

    context.user_data["last_choice"] = choice

    # Confirm / Cancel
    if choice == "Confirm Transaction":
        save_request(
            update.effective_user.id,
            context.user_data.get("username", ""),
            context.user_data.get("project_type", "Unknown"),
            context.user_data.get("project_details", "")
        )
        return await goto_state(update, context, "request_confirmation")

    if choice == "Cancel Request":
        context.user_data.clear()
        return await goto_state(update, context, "start")

    # Request Service / Back buttons on description
    if choice == "Request Service":
        return await goto_state(update, context, "request_details")
    if choice == "Back":
        parent_state = context.user_data.get("parent_state", "start")
        return await goto_state(update, context, parent_state)

    # Navigate normally
    if choice in state.get("next", {}):
        next_state = state["next"][choice]

        # If next_state has description, save parent & project type
        if "description" in CONVO.get(next_state, {}):
            context.user_data["parent_state"] = state_name
            context.user_data["project_type"] = choice

        return await goto_state(update, context, next_state)

    # Default: refresh current state
    return await goto_state(update, context, state_name)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    return await goto_state(update, context, "start")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state_name = context.user_data.get("state_name", "start")
    if state_name == "request_details":
        context.user_data["project_details"] = update.message.text.strip()
        return await goto_state(update, context, "request_username")
    elif state_name == "request_username":
        context.user_data["username"] = update.message.text.strip().lstrip("@")
        return await goto_state(update, context, "request_summary")
    await update.message.reply_text("⚠️ Please use the menu buttons.")
# --------------------------------

# ---------- MAIN ----------
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # Collect all states dynamically from JSON
    all_states = {k: [CallbackQueryHandler(handle_callback)] for k in CONVO.keys()}
    # Add text handlers for input states
    for t_state in ["request_details", "request_username"]:
        all_states[t_state] = [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)]

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states=all_states,
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv_handler)
    logger.info("🤖 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
