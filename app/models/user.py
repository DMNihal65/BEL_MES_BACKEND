from pony.orm import Required, Set, PrimaryKey, Optional
from datetime import datetime
from ..database.connection import db

class UserRole(db.Entity):
    # _table_ = 'user_roles'
    _table_ = ("user_schema", "user_roles") 
    id = PrimaryKey(int, auto=True)
    role_name = Required(str, unique=True)  # 'admin', 'user', etc.
    access_list = Required(str)  # JSON string of access permissions
    created_at = Required(datetime, default=datetime.utcnow)
    users = Set('User')

class User(db.Entity):
    _table_ = ("user_schema", "users") 
    id = PrimaryKey(int, auto=True)
    email = Required(str, unique=True)
    username = Required(str, unique=True)
    hashed_password = Required(str)
    role = Required(UserRole)  # Changed to reference UserRole
    created_at = Required(datetime, default=datetime.utcnow)
    is_active = Required(bool, default=True) 