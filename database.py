# database.py
import mysql.connector
from mysql.connector import pooling
from urllib.parse import urlparse
import os
from dotenv import load_dotenv

load_dotenv()

# Railway gives you DATABASE_URL (MySQL)
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("❌ DATABASE_URL not set in environment variables.")

# Parse DATABASE_URL
url = urlparse(DATABASE_URL)

dbconfig = {
    "host": url.hostname,
    "user": url.username,
    "password": url.password,
    "database": url.path[1:],  # remove leading '/'
    "port": url.port or 3306
}

# Connection Pool
connection_pool = pooling.MySQLConnectionPool(
    pool_name="mypool",
    pool_size=5,
    **dbconfig
)

def init_db():
    """Initialize database tables if not exist."""
    conn = connection_pool.get_connection()
    cursor = conn.cursor()

    # Request table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS request (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT NOT NULL,
            project_type VARCHAR(255) NOT NULL,
            details TEXT NOT NULL,
            username VARCHAR(255),
            status VARCHAR(50) DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Provide table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS provide (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_id BIGINT NOT NULL,
            service_category VARCHAR(255) NOT NULL,
            subservice VARCHAR(255) NOT NULL,
            description TEXT,
            username VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    cursor.close()
    conn.close()


# ---------------- CRUD HELPERS ---------------- #

# Request Table
def add_request(user_id, project_type, details, username):
    conn = connection_pool.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO request (user_id, project_type, details, username)
        VALUES (%s, %s, %s, %s)
    """, (user_id, project_type, details, username))
    conn.commit()
    cursor.close()
    conn.close()


def get_requests_by_user(user_id):
    conn = connection_pool.get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM request WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def update_request_status(request_id, status):
    conn = connection_pool.get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE request SET status = %s WHERE id = %s", (status, request_id))
    conn.commit()
    cursor.close()
    conn.close()


def delete_request(request_id):
    conn = connection_pool.get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM request WHERE id = %s", (request_id,))
    conn.commit()
    cursor.close()
    conn.close()


# Provide Table
def add_provide(user_id, service_category, subservice, description, username):
    conn = connection_pool.get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO provide (user_id, service_category, subservice, description, username)
        VALUES (%s, %s, %s, %s, %s)
    """, (user_id, service_category, subservice, description, username))
    conn.commit()
    cursor.close()
    conn.close()


def get_provides_by_user(user_id):
    conn = connection_pool.get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM provide WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def delete_provide(provide_id):
    conn = connection_pool.get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM provide WHERE id = %s", (provide_id,))
    conn.commit()
    cursor.close()
    conn.close()
