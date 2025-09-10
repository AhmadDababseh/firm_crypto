import json
import logging
import os
import re
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
load_dotenv()  # Local dev; Railway injects env vars automatically
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Railway MySQL URL (single connection string)
# Example: mysql://user:pass@host:3306/dbname
DB_URL = os.getenv("MYSQL_URL")

# Path to JSON conversation
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONVO_FILE = os.path.join(BASE_DIR, "conversation.json")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

with open(CONVO_FILE, "r", encoding="utf-8") as f:
    CONVO = json.load(f)

# ---------- DATABASE ----------
def parse_mysql_url(url: str):
    """Parse a MySQL connection string into connection parameters."""
    match = re.match(r"mysql:\/\/(.*?):(.*?)@(.*?):(\d+)\/(.*)", url)
    if not match:
        raise ValueError("Invalid MySQL URL format")
    user, password, host, port, db = match.groups()
    return {
        "user": user,
        "password": password,
        "host": host,
        "port": int(port),
        "database": db,
    }

def get_connection():
    creds = parse_mysql_url(DB_URL)
    return mysql.connector.connect(**creds)

def init_db():
    """Initialize the database and ensure the 'requests' table exists."""
    try:
        creds = parse_mysql_url(DB_URL)
        conn = mysql.connector.connect(
            host=creds["host"],
            user=creds["user"],
            password=creds["password"],
            port=creds["port"],
        )
        cur = conn.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS {creds['database']}")
        conn.database = creds["database"]

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
        logger.info("✅ Database initialized successfully")
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

    if choice == "Request Service":
        return await goto_state(update, context, "request_details")
    if choice == "Back":
        parent_state = context.user_data.get("parent_state", "start")
        return await goto_state(update, context, parent_state)

    if choice in state.get("next", {}):
        next_state = state["next"][choice]
        if "description" in CONVO.get(next_state, {}):
            context.user_data["parent_state"] = state_name
            context.user_data["project_type"] = choice
        return await goto_state(update, context, next_state)

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

# ---------- MAIN ----------
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    all_states = {k: [CallbackQueryHandler(handle_callback)] for k in CONVO.keys()}
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
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
