from pony.orm import db_session
import psycopg2
from ..config.settings import settings

def init_database():
    """Initialize database schemas"""
    conn = psycopg2.connect(
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        host=settings.DB_HOST,
        port=settings.DB_PORT
    )
    conn.autocommit = True
    cursor = conn.cursor()

    # Create schemas if they don't exist
    schemas = [
        "auth",
        "master_order",
        "document_management",
        "hr",
        "finance",
        "inventoryv1",
        "inventory",
        "document_management_v2"
    ]

    for schema in schemas:
        try:
            cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}";')
            print(f"Schema '{schema}' created or already exists")
        except Exception as e:
            print(f"Error creating schema '{schema}': {str(e)}")

    cursor.close()
    conn.close() 