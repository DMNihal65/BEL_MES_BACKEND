from pony.orm import Required, Set, PrimaryKey, Optional
from datetime import datetime
from ..database.connection import db

class UserRole(db.Entity):
    _table_ = ("auth", "user_roles")
    id = PrimaryKey(int, auto=True)
    role_name = Required(str, unique=True)
    access_list = Required(str)
    created_at = Required(datetime, default=datetime.utcnow)
    users = Set('User')

class User(db.Entity):
    _table_ = ('auth', 'users')
    id = PrimaryKey(int, auto=True)
    email = Required(str, unique=True)
    username = Required(str, unique=True)
    hashed_password = Required(str)
    role = Required('UserRole')
    created_at = Required(datetime, default=datetime.utcnow)
    is_active = Required(bool, default=True)
    # Add reverse relationships
    user_logs = Set('UserLogs', reverse='user')
    documents = Set('Document', reverse='created_by')
    doc_folders = Set('DocFolder', reverse='created_by')
    document_access_logs = Set('DocumentAccessLog', reverse='user')
    document_versions = Set('DocumentVersion', reverse='created_by')