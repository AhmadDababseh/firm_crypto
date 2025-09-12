# bot.py
import json
import logging
import os
import re
import time
from urllib.parse import urlparse, unquote
import mysql.connector
from mysql.connector import Error
from mysql.connector import pooling
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
load_dotenv()  # local dev; Railway injects env vars automatically
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_POOL = None  # global pool
POOL_NAME = "bot_pool"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONVO_FILE = os.path.join(BASE_DIR, "conversation.json")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

with open(CONVO_FILE, "r", encoding="utf-8") as f:
    CONVO = json.load(f)


# ---------- DB helpers ----------
def parse_mysql_url_with_urllib(url: str):
    """Parse mysql://user:pass@host:port/dbname using urllib.parse and unquote."""
    parsed = urlparse(url)
    if parsed.scheme not in ("mysql", "mysql+pymysql", "mysql+mysqlconnector", "mysql+mysqldb", "mysql+ours"):
        # still try to parse if scheme missing, but warn
        logger.debug("parse_mysql_url_with_urllib: unexpected scheme %s", parsed.scheme)
    user = unquote(parsed.username) if parsed.username else None
    password = unquote(parsed.password) if parsed.password else None
    host = parsed.hostname
    port = parsed.port or 3306
    database = parsed.path.lstrip("/") if parsed.path else None
    return {
        "user": user,
        "password": password,
        "host": host,
        "port": port,
        "database": database,
    }


def get_db_creds():
    """Get DB credentials from MYSQL_URL or individual MYSQL* env vars (Railway provides both)."""
    url = os.getenv("MYSQL_URL") or os.getenv("DATABASE_URL") or os.getenv("CLEARDB_DATABASE_URL")
    if url:
        creds = parse_mysql_url_with_urllib(url)
        # if some parts are missing, fallback to individual vars
        creds["user"] = creds.get("user") or os.getenv("MYSQLUSER") or os.getenv("MYSQL_USER")
        creds["password"] = creds.get("password") or os.getenv("MYSQLPASSWORD") or os.getenv("MYSQL_PASSWORD")
        creds["host"] = creds.get("host") or os.getenv("MYSQLHOST") or os.getenv("MYSQL_HOST")
        creds["port"] = creds.get("port") or int(os.getenv("MYSQLPORT") or os.getenv("MYSQL_PORT") or 3306)
        creds["database"] = creds.get("database") or os.getenv("MYSQLDATABASE") or os.getenv("MYSQL_DATABASE")
        return creds

    # fallback to individual env vars (try several common names)
    return {
        "user": os.getenv("MYSQLUSER") or os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQLPASSWORD") or os.getenv("MYSQL_PASSWORD"),
        "host": os.getenv("MYSQLHOST") or os.getenv("MYSQL_HOST"),
        "port": int(os.getenv("MYSQLPORT") or os.getenv("MYSQL_PORT") or 3306),
        "database": os.getenv("MYSQLDATABASE") or os.getenv("MYSQL_DATABASE"),
    }


def ensure_pool():
    """Create a connection pool if it doesn't exist."""
    global DB_POOL
    if DB_POOL:
        return

    creds = get_db_creds()
    if not creds.get("host") or not creds.get("user") or not creds.get("password"):
        raise ValueError("Database credentials are missing. Please set MYSQL_URL or MYSQL* env vars.")

    pool_size = int(os.getenv("DB_POOL_SIZE", "3"))  # conservative default
    DB_POOL = pooling.MySQLConnectionPool(
        pool_name=POOL_NAME,
        pool_size=pool_size,
        host=creds["host"],
        user=creds["user"],
        password=creds["password"],
        port=creds["port"],
        database=creds["database"],
        autocommit=False,
    )
    logger.info("DB pool created (size=%s)", pool_size)


def get_connection():
    """Get a connection from the pool (creates pool on demand)."""
    ensure_pool()
    return DB_POOL.get_connection()


# ---------- DB init / migrations ----------
def init_db(retry_seconds: int = 1, max_retries: int = 6):
    """
    Initialize the database:
    1. Connect without a database to CREATE DATABASE IF NOT EXISTS
    2. Create requests table
    3. Create connection pool
    """
    creds = get_db_creds()
    if not creds.get("host"):
        logger.error("No DB host found in env variables.")
        return

    attempt = 0
    while attempt < max_retries:
        try:
            # Connect to server (no database) to ensure DB exists
            conn_kwargs = {
                "host": creds["host"],
                "user": creds["user"],
                "password": creds["password"],
                "port": creds["port"],
            }
            conn = mysql.connector.connect(**conn_kwargs)
            cur = conn.cursor()
            # sanitize DB name (simple): allow letters/numbers/underscore/dash
            db_name = creds["database"] or "railway"
            if not re.match(r"^[\w\-]+$", db_name):
                logger.warning("Database name contains unusual characters; using raw value anyway.")
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}`")
            conn.commit()
            cur.close()
            conn.close()

            # Now create a pool (this will use 'database' param)
            # Reset any existing pool pointer so ensure_pool recreates it
            global DB_POOL
            DB_POOL = None
            ensure_pool()

            # Make sure table exists
            conn2 = get_connection()
            cur2 = conn2.cursor()
            cur2.execute("""
                CREATE TABLE IF NOT EXISTS requests (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id BIGINT,
                    username VARCHAR(255),
                    project_type VARCHAR(255),
                    details TEXT,
                    status VARCHAR(50) DEFAULT 'Pending'
                )
            """)
            conn2.commit()
            cur2.close()
            conn2.close()

            logger.info("✅ Database initialized successfully")
            return
        except Error as e:
            attempt += 1
            logger.warning("DB init attempt %s/%s failed: %s", attempt, max_retries, e)
            time.sleep(retry_seconds * attempt)

    logger.error("Failed to initialize DB after %s attempts.", max_retries)


# ---------- CRUD helpers ----------
def save_request(user_id, username, project_type, details):
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO requests (user_id, username, project_type, details) VALUES (%s, %s, %s, %s)",
            (user_id, username, project_type, details),
        )
        conn.commit()
        cur.close()
    except Error as e:
        logger.error(f"MySQL insert error: {e}")
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn:
            conn.close()


def get_user_requests(user_id):
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT id, project_type, status FROM requests WHERE user_id = %s", (user_id,))
        rows = cur.fetchall()
        cur.close()
        return rows
    except Error as e:
        logger.error(f"MySQL fetch error: {e}")
        return []
    finally:
        if conn:
            conn.close()


# ---------- HANDLERS ----------
async def goto_state(update, context, state_name: str, new_message: bool = True):
    state = CONVO.get(state_name)
    if not state:
        await update.message.reply_text("⚠️ Unknown state. Returning to main menu.")
        state_name = "start"
        state = CONVO[state_name]

    context.user_data["state_name"] = state_name
    text = state.get("message", "")

    if state_name == "request_summary":
        text = text.format(
            project_type=context.user_data.get("project_type", "Unknown"),
            project_details=context.user_data.get("project_details", ""),
            username=context.user_data.get("username", "")
        )

    if state_name == "my_requests":
        requests = get_user_requests(update.effective_user.id)
        if requests:
            text += "\n\n" + "\n".join([f"#{r[0]} | {r[1]} | {r[2]}" for r in requests])
        else:
            text += "\n\n(No requests yet.)"

    if "description" in state:
        text = state["description"]
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("Request Service", callback_data="Request Service")],
            [InlineKeyboardButton("Back", callback_data="Back")]
        ])
    else:
        buttons = [[InlineKeyboardButton(opt, callback_data=opt)] for opt in state.get("options", [])]
        reply_markup = InlineKeyboardMarkup(buttons) if buttons else None

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
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is missing. Set it in env vars.")
        raise SystemExit(1)

    # initialize DB (create DB and tables if missing)
    init_db()

    # Build app and attach DB shutdown hook
    async def _post_shutdown(application: Application) -> None:
        logger.info("Post-shutdown hook: releasing DB pool")
        # mysql.connector pooling does not provide a direct 'close pool' API.
        # Closing active connections is handled by each connection.close() used above.
        # We null the global pool reference to allow GC.
        global DB_POOL
        DB_POOL = None

    app = Application.builder().token(BOT_TOKEN).post_shutdown(_post_shutdown).build()

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
    logger.info("🤖 Bot is running (polling)...")
    # run_polling will handle SIGTERM/SIGINT and perform graceful shutdown
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
