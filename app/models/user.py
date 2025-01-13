from pony.orm import Required, Set, PrimaryKey
from datetime import datetime
from ..core.database import db

class User(db.Entity):
    id = PrimaryKey(int, auto=True)
    email = Required(str, unique=True)
    username = Required(str, unique=True)
    hashed_password = Required(str)
    role = Required(str)  # 'admin', 'user', etc.
    access_list = Required(str)  # JSON string of access permissions
    created_at = Required(datetime, default=datetime.utcnow)
    is_active = Required(bool, default=True) 