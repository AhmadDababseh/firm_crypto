import os
import urllib.parse
import logging
import mysql.connector
from mysql.connector import pooling
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
# -------------------------------------------------------
# Configuration
# -------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Railway provides a single connection URL, e.g.:
# mysql://username:password@containers-us-west-123.railway.app:3306/mydb
MYSQL_URL = os.getenv("DATABASE_URL")
if not MYSQL_URL:
    raise SystemExit("❌ MYSQL_URL not found in environment variables")

url = urllib.parse.urlparse(MYSQL_URL)

DB_CONFIG = {
    "host": url.hostname,
    "port": url.port or 3306,
    "user": url.username,
    "password": url.password,
    "database": url.path.lstrip("/"),
    "autocommit": True,
}

# Create a small connection pool for re-use
POOL = pooling.MySQLConnectionPool(pool_name="bot_pool", pool_size=5, **DB_CONFIG)


def get_conn():
    """Fetch a pooled DB connection."""
    return POOL.get_connection()


# -------------------------------------------------------
# Database Initialization
# -------------------------------------------------------
def init_db():
    """
    Create required tables if they don't exist.
    Tables:
        - requests   : user service requests
        - submissions: provider service submissions
    """
    create_requests = """
    CREATE TABLE IF NOT EXISTS requests (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id BIGINT NOT NULL,
        username VARCHAR(255),
        category VARCHAR(255),
        service VARCHAR(255),
        details TEXT,
        status VARCHAR(50) DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """

    create_submissions = """
    CREATE TABLE IF NOT EXISTS submissions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id BIGINT NOT NULL,
        username VARCHAR(255),
        category VARCHAR(255),
        service VARCHAR(255),
        experience TEXT,
        status VARCHAR(50) DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(create_requests)
        cur.execute(create_submissions)
        conn.commit()
        logger.info("✅ Tables ensured (requests, submissions).")
    finally:
        conn.close()


# -------------------------------------------------------
# Insert Helpers
# -------------------------------------------------------
def add_request(user_id: int, username: str, category: str, service: str, details: str):
    """
    Store a new request into the requests table.
    """
    conn = get_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            INSERT INTO requests (user_id, username, category, service, details)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, username, category, service, details),
        )
        conn.commit()
        logger.info(f"💾 Added request for user {user_id}")
    finally:
        conn.close()


def add_submission(user_id: int, username: str, category: str, service: str, experience: str):
    """
    Store a new submission into the submissions table.
    """
    conn = get_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            INSERT INTO submissions (user_id, username, category, service, experience)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, username, category, service, experience),
        )
        conn.commit()
        logger.info(f"💾 Added submission for user {user_id}")
    finally:
        conn.close()


# -------------------------------------------------------
# Retrieval Helpers
# -------------------------------------------------------
def get_requests_by_user(user_id: int):
    """
    Return a list of all requests by a given user.
    Each row is a dict with keys:
    id, category, service, details, status, created_at
    """
    conn = get_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT id, category, service, details, status, created_at
            FROM requests
            WHERE user_id = %s
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def get_submissions_by_user(user_id: int):
    """
    Return a list of all submissions by a given user.
    """
    conn = get_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT id, category, service, experience, status, created_at
            FROM submissions
            WHERE user_id = %s
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        return cur.fetchall()
    finally:
        conn.close()
