from datetime import datetime
from pony.orm import *
from ..database.connection import db
from .user import User
from .master_order import Order


class FolderV2(db.Entity):
    """Represents a folder in the document management system"""
    _table_ = ("document_management_v2", "folders")

    id = PrimaryKey(int, auto=True)
    name = Required(str)
    path = Required(str, unique=True)  # Full path including parent folders
    parent_folder = Optional("FolderV2", nullable=True, column='parent_folder_id')
    child_folders = Set("FolderV2", reverse='parent_folder')
    created_at = Required(datetime, default=datetime.utcnow)
    created_by = Required(User, reverse='doc_folders_v2', column='created_by_id')
    is_active = Required(bool, default=True)
    documents = Set('DocumentV2')


class DocumentTypeV2(db.Entity):
    """Defines different types of documents and their allowed extensions"""
    _table_ = ("document_management_v2", "document_types")

    id = PrimaryKey(int, auto=True)
    name = Required(str, unique=True)  # e.g., 'MPP', 'IPID', 'Engineering Drawing'
    description = Optional(str)
    allowed_extensions = Required(Json)  # e.g., [".pdf", ".dwg", ".dxf"]
    is_active = Required(bool, default=True)
    documents = Set('DocumentV2')


class DocumentV2(db.Entity):
    """Main document entity that can be linked to folders, part numbers, or production orders"""
    _table_ = ("document_management_v2", "documents")

    id = PrimaryKey(int, auto=True)
    name = Required(str)
    folder = Required(FolderV2, column='folder_id_v2')  # Added explicit column name
    doc_type = Required(DocumentTypeV2, column='doc_type_id_v2')  # Added explicit column name
    description = Optional(str)

    # Optional links - a document can be linked to either part_number, production_order, or neither
    part_number = Optional(str, nullable=True)  # Store the actual part number string
    production_order = Optional(Order, nullable=True, reverse='documents_v2', column='production_order_id_v2')

    created_at = Required(datetime, default=datetime.utcnow)
    created_by = Required(User, reverse='documents_v2', column='created_by_id_v2')
    is_active = Required(bool, default=True)

    latest_version = Optional('DocumentVersionV2', nullable=True, reverse='latest_of', column='latest_version_id_v2')
    versions = Set('DocumentVersionV2', reverse='document')
    access_logs = Set('DocumentAccessLogV2')


class DocumentVersionV2(db.Entity):
    """Stores version information for documents"""
    _table_ = ("document_management_v2", "document_versions")

    id = PrimaryKey(int, auto=True)
    document = Required(DocumentV2, reverse='versions', column='document_id_v2')
    latest_of = Optional(DocumentV2, nullable=True, reverse='latest_version', column='latest_of_id_v2')
    version_number = Required(str)
    minio_path = Required(str, unique=True)
    file_size = Required(int)
    checksum = Required(str)
    metadata = Optional(Json)

    created_at = Required(datetime, default=datetime.utcnow)
    created_by = Required(User, reverse='document_versions_v2', column='created_by_id_v2')
    is_active = Required(bool, default=True)

    access_logs = Set('DocumentAccessLogV2')


class DocumentAccessLogV2(db.Entity):
    """Tracks all document access and modifications"""
    _table_ = ("document_management_v2", "document_access_logs")

    id = PrimaryKey(int, auto=True)
    document = Required(DocumentV2, column='document_id_v2')
    version = Optional(DocumentVersionV2, nullable=True, column='version_id_v2')
    user = Required(User, reverse='document_access_logs_v2', column='user_id_v2')
    action_type = Required(str)  # 'VIEW', 'DOWNLOAD', 'UPDATE', 'DELETE'
    action_timestamp = Required(datetime, default=datetime.utcnow)
    ip_address = Optional(str, nullable=True) 