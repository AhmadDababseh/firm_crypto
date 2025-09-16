import os
import re
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv

# ----------------- LOAD ENV VARIABLES -----------------
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")  # Format: mysql://USER:PASSWORD@HOST:PORT/DBNAME

# ----------------- PARSE RAILWAY URL -----------------
def parse_database_url(url):
    """Parses the Railway MySQL URL into components for mysql-connector."""
    pattern = re.compile(
        r"mysql:\/\/(?P<user>.*?):(?P<password>.*?)@(?P<host>.*?):(?P<port>\d+)\/(?P<dbname>.*?)$"
    )
    match = pattern.match(url)
    if not match:
        raise ValueError("Invalid DATABASE_URL format")
    return match.groupdict()

db_config = parse_database_url(DATABASE_URL)

# ----------------- CONNECTION -----------------
def get_connection():
    """Get a new DB connection."""
    return mysql.connector.connect(
        host=db_config["host"],
        user=db_config["user"],
        password=db_config["password"],
        database=db_config["dbname"],
        port=int(db_config["port"]),
    )

# ----------------- INIT TABLES -----------------
def init_db():
    """Create tables if they don’t exist."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INT AUTO_INCREMENT PRIMARY KEY,
            telegram_id VARCHAR(50) NOT NULL,
            scam_name VARCHAR(255) NOT NULL,
            scam_link VARCHAR(500) NOT NULL,
            scam_owner VARCHAR(255),
            scam_description TEXT,
            scam_date VARCHAR(50),
            scammed_amount VARCHAR(50),
            scam_token VARCHAR(100),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS services (
            id INT AUTO_INCREMENT PRIMARY KEY,
            telegram_id VARCHAR(50) NOT NULL,
            service_type VARCHAR(100) NOT NULL, -- e.g., "contact", "verification"
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    cursor.close()
    conn.close()

# ----------------- REQUESTS FUNCTIONS -----------------
def add_request(telegram_id, data: dict):
    """Insert a new scam report request."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        INSERT INTO requests 
        (telegram_id, scam_name, scam_link, scam_owner, scam_description, scam_date, scammed_amount, scam_token)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    values = (
        str(telegram_id),
        data.get("scam_name"),
        data.get("scam_link"),
        data.get("scam_owner"),
        data.get("scam_description"),
        data.get("scam_date"),
        data.get("scammed_amount"),
        data.get("scam_token"),
    )

    cursor.execute(query, values)
    conn.commit()
    request_id = cursor.lastrowid

    cursor.close()
    conn.close()
    return request_id

def get_requests():
    """Retrieve all scam report requests."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM requests ORDER BY created_at DESC")
    results = cursor.fetchall()
    cursor.close()
    conn.close()
    return results

# ----------------- SERVICES FUNCTIONS -----------------
def add_service(telegram_id, service_type, message):
    """Insert a new service submission (contact, verification, etc.)."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        INSERT INTO services (telegram_id, service_type, message)
        VALUES (%s, %s, %s)
    """
    values = (str(telegram_id), service_type, message)

    cursor.execute(query, values)
    conn.commit()
    service_id = cursor.lastrowid

    cursor.close()
    conn.close()
    return service_id

def get_services():
    """Retrieve all service submissions."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM services ORDER BY created_at DESC")
    results = cursor.fetchall()
    cursor.close()
    conn.close()
    return results
