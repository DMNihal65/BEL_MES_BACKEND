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
    reschedule_histories = Set('RescheduleHistory', reverse='rescheduled_by_operator')
    production_logs = Set('ProductionLog', reverse='operator')
    documents = Set('Document', reverse='created_by')
    doc_folders = Set('DocFolder', reverse='created_by')
    document_access_logs = Set('DocumentAccessLog', reverse='user')
    document_versions = Set('DocumentVersion', reverse='created_by')
    inventory_categories = Set('InventoryCategory', reverse='created_by')
    inventory_subcategories = Set('InventorySubCategory', reverse='created_by')
    inventory_items = Set('InventoryItem', reverse='created_by')
    calibration_schedules = Set('CalibrationSchedule', reverse='created_by')
    calibration_histories = Set('CalibrationHistory', reverse='performed_by')
    inventory_requests = Set('InventoryRequest', reverse='requested_by')
    approved_inventory_requests = Set('InventoryRequest', reverse='approved_by')
    inventory_transactions = Set('InventoryTransaction', reverse='performed_by')
    # New reverse relationships for document management V2
    doc_folders_v2 = Set('FolderV2', reverse='created_by')
    documents_v2 = Set('DocumentV2', reverse='created_by')
    document_versions_v2 = Set('DocumentVersionV2', reverse='created_by')
    document_access_logs_v2 = Set('DocumentAccessLogV2', reverse='user')


class MachineCredential(db.Entity):
    _table_ = ("auth", "machine_credentials")
    id = PrimaryKey(int, auto=True)
    machine = Required('Machine', unique=True, reverse='credential')  # ✅ reverse declared
    password = Required(str)