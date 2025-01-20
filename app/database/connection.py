from pony.orm import Database, db_session
from ..config.settings import settings

# Create a single database instance
db = Database()

def connect_to_db():
    db.bind(
        provider='postgres',
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        database=settings.DB_NAME
    )
    
    # Create schemas if they don't exist
    db.execute("CREATE SCHEMA IF NOT EXISTS hr_schema")
    db.execute("CREATE SCHEMA IF NOT EXISTS finance_schema")
    db.execute("CREATE SCHEMA IF NOT EXISTS user_schema")
    db.execute("CREATE SCHEMA IF NOT EXISTS master_order")
    db.execute("CREATE SCHEMA IF NOT EXISTS inventory")
    db.execute("CREATE SCHEMA IF NOT EXISTS document_management")
    # db.execute("CREATE SCHEMA IF NOT EXISTS mpp")
    
    # Import all models to ensure they're registered with the database
    from ..models import hr_models, finance_models, master_order, user
    
    # Generate mapping after all models are imported
    db.generate_mapping(create_tables=True) 