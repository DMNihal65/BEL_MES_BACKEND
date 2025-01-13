from pony.orm import Database, db_session
from ..core.config import settings

db = Database()

def init_db():
    db.bind(provider='postgres', dsn=settings.DATABASE_URL)
    db.generate_mapping(create_tables=True) 