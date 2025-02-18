from pony.orm import Database, db_session
from ..config.settings import settings
import os
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Create a single database instance
db = Database()

def connect_to_db():
    """Connect to the database and return connection details"""
    try:
        # Get database configuration from environment variables
        db_host = os.getenv('DB_HOST', '172.18.7.89')
        db_port = os.getenv('DB_PORT', '5432')
        db_name = os.getenv('DB_NAME', 'bel_mes')
        db_user = os.getenv('DB_USER', 'postgres')
        db_password = os.getenv('DB_PASSWORD', 'postgres')

        # Store connection details for logging
        app_state = connect_to_db.app_state if hasattr(connect_to_db, 'app_state') else None
        if app_state:
            app_state.db_host = db_host
            app_state.db_name = db_name
            app_state.db_user = db_user

        # Connect to database
        db.bind(provider='postgres',
                user=db_user,
                password=db_password,
                host=db_host,
                port=db_port,
                database=db_name)

        # Create schemas if they don't exist
        db.execute("CREATE SCHEMA IF NOT EXISTS hr_schema")
        db.execute("CREATE SCHEMA IF NOT EXISTS finance_schema")
        db.execute("CREATE SCHEMA IF NOT EXISTS user_schema")
        db.execute("CREATE SCHEMA IF NOT EXISTS master_order")
        db.execute("CREATE SCHEMA IF NOT EXISTS inventory")
        db.execute("CREATE SCHEMA IF NOT EXISTS scheduling")
        db.execute("CREATE SCHEMA IF NOT EXISTS inventoryv1")
        db.execute("CREATE SCHEMA IF NOT EXISTS document_management")
        db.execute("CREATE SCHEMA IF NOT EXISTS auth")
        db.execute("CREATE SCHEMA IF NOT EXISTS document_management_v2")

        # db.execute("CREATE SCHEMA IF NOT EXISTS mpp")

        # Import all models to ensure they're registered with the database
        from ..models import hr_models, finance_models, master_order, user, document_management_v2
        
        # Generate mapping after all models are imported
        db.generate_mapping(create_tables=True)

        # Log successful connection details
        logger.info(f"Database connection details:")
        logger.info(f"Provider: PostgreSQL")
        logger.info(f"Host: {db_host}")
        logger.info(f"Port: {db_port}")
        logger.info(f"Database: {db_name}")
        logger.info(f"User: {db_user}")
        logger.info(f"Connected at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        return db

    except Exception as e:
        logger.error(f"Database connection error: {str(e)}")
        raise

    finally:
        # Ensure the database connection is closed
        db.disconnect() 