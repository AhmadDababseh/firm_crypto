import os
import logging
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------
# ⚙️  Database Configuration
# --------------------------------------------------------------------
DB_URL = os.getenv("DATABASE_URL")  # e.g. mysql://user:pass@host:port/dbname
# Railway usually provides a URL like:
# mysql://USERNAME:PASSWORD@HOST:PORT/DATABASE

# Helper to parse Railway-style URL if needed
def parse_mysql_url(url: str):
    # mysql://user:pass@host:port/db
    url = url.replace("mysql://", "")
    creds, host_db = url.split("@")
    user, pwd = creds.split(":", 1)
    host_port, dbname = host_db.split("/", 1)
    if ":" in host_port:
        host, port = host_port.split(":", 1)
    else:
        host, port = host_port, "3306"
    return {
        "user": user,
        "password": pwd,
        "host": host,
        "port": int(port),
        "database": dbname
    }


# --------------------------------------------------------------------
# 🔗 Connection Helper
# --------------------------------------------------------------------
def get_connection():
    cfg = parse_mysql_url(DB_URL)
    return mysql.connector.connect(**cfg)


# --------------------------------------------------------------------
# 🗂️ Database Initialization
# --------------------------------------------------------------------
def init_db():
    """
    Ensure tables exist:
      - requests: stores both 'requests' and 'submissions'
    """
    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS requests (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                username VARCHAR(255),
                category VARCHAR(255),
                service VARCHAR(255),
                details TEXT,
                req_type ENUM('Request','Submission') DEFAULT 'Request',
                status VARCHAR(50) DEFAULT 'Pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ Database initialized successfully.")
    except Error as e:
        logger.error(f"❌ Failed to initialize DB: {e}")


# --------------------------------------------------------------------
# ➕ Insert Data
# --------------------------------------------------------------------
def add_request(user_id: int, username: str, category: str, service: str,
                details: str, req_type: str = "Request"):
    """
    Insert a new record (request or submission) into the database.
    """
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO requests (user_id, username, category, service, details, req_type)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (user_id, username, category, service, details, req_type)
        )
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"✅ {req_type} saved for user {user_id}")
    except Error as e:
        logger.error(f"❌ Failed to add request: {e}")


# --------------------------------------------------------------------
# 📥 Retrieve Data
# --------------------------------------------------------------------
def get_requests_by_user(user_id: int):
    """
    Retrieve all requests/submissions by a specific user.
    Returns a list of dictionaries.
    """
    rows = []
    try:
        conn = get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT id, category, service, details, status, req_type, created_at
            FROM requests
            WHERE user_id = %s
            ORDER BY created_at DESC
            """,
            (user_id,)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Error as e:
        logger.error(f"❌ Failed to get requests: {e}")
    return rows
