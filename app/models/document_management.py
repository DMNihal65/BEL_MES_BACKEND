from datetime import datetime
from pony.orm import *
from ..database.connection import db
from .user import User
from .master_order import Order

class DocFolder(db.Entity):
    _table_ = ("document_management", "doc_folders")
    id = PrimaryKey(int, auto=True)
    parent_folder = Optional(int, nullable=True)
    folder_name = Required(str)
    folder_path = Required(str, unique=True)
    created_at = Required(datetime, default=datetime.utcnow)
    created_by = Required(User)
    is_active = Required(bool, default=True)
    documents = Set('Document')

class DocType(db.Entity):
    _table_ = ("document_management", "doc_types")
    id = PrimaryKey(int, auto=True)
    type_name = Required(str, unique=True)
    description = Optional(str)
    file_extensions = Required(Json)
    is_active = Required(bool, default=True)
    documents = Set('Document')

class Document(db.Entity):
    _table_ = ("document_management", "documents")
    id = PrimaryKey(int, auto=True)
    folder = Required(DocFolder)
    part_number_id = Required(Order)  # Link to Order model
    doc_type = Required(DocType)
    document_name = Required(str)
    description = Optional(str)
    minio_path = Required(str)
    created_at = Required(datetime, default=datetime.utcnow)
    created_by = Required(User)
    is_active = Required(bool, default=True)
    latest_version = Optional('DocumentVersion', reverse='latest_of')
    versions = Set('DocumentVersion', reverse='document')
    mpps = Set('MPP')
    access_logs = Set('DocumentAccessLog', reverse='document')
    

class DocumentVersion(db.Entity):
    _table_ = ("document_management", "document_versions")
    id = PrimaryKey(int, auto=True)
    document = Required(Document, reverse='versions')
    latest_of = Optional(Document, reverse='latest_version')
    version_number = Required(str)
    minio_object_id = Required(str)
    file_size = Required(int)
    checksum = Required(str)
    metadata = Optional(Json)
    created_at = Required(datetime, default=datetime.utcnow)
    created_by = Required(User)
    status = Required(str)
    access_logs = Set('DocumentAccessLog', reverse='version')

class DocumentAccessLog(db.Entity):
    _table_ = ("document_management", "document_access_logs")
    id = PrimaryKey(int, auto=True)
    document = Optional(Document, reverse='access_logs')
    version = Optional(DocumentVersion, reverse='access_logs')
    user = Required(User)
    action_type = Required(str)
    action_timestamp = Required(datetime, default=datetime.utcnow)
    ip_address = Optional(str)