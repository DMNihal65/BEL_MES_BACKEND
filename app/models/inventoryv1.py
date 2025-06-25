from datetime import datetime
from pony.orm import *
from decimal import Decimal
from ..database.connection import db  # Import the shared db instance


class InventoryCategory(db.Entity):
    """Main categories like Tools, Gauges, Fixtures, etc."""
    _table_ = ("inventoryv1", "categories")
    id = PrimaryKey(int, auto=True)
    name = Required(str)  # e.g., "Tools", "Gauges"
    description = Optional(str)
    subcategories = Set('InventorySubCategory', reverse='category')
    created_at = Required(datetime, default=datetime.utcnow)
    created_by = Required('User', reverse='inventory_categories')


class InventorySubCategory(db.Entity):
    """Sub-categories like EndMills, Drills, Inserts, etc."""
    _table_ = ("inventoryv1", "subcategories")
    id = PrimaryKey(int, auto=True)
    category = Required(InventoryCategory, reverse='subcategories')
    name = Required(str)  # e.g., "EndMills", "Drills"
    description = Optional(str)
    dynamic_fields = Required(Json)  # Stores the field definitions
    items = Set('InventoryItem', reverse='subcategory')
    created_at = Required(datetime, default=datetime.utcnow)
    created_by = Required('User', reverse='inventory_subcategories')


class InventoryItem(db.Entity):
    """Individual inventory items with dynamic fields"""
    _table_ = ("inventoryv1", "items")
    id = PrimaryKey(int, auto=True)
    subcategory = Required(InventorySubCategory, reverse='items')
    item_code = Required(str, unique=True)  # Unique identifier for the item
    dynamic_data = Required(Json)  # Stores the actual values for dynamic fields
    quantity = Required(int)
    available_quantity = Required(int)
    calibrations = Set('CalibrationSchedule', reverse='inventory_item')
    transactions = Set('InventoryTransaction', reverse='inventory_item')
    requests = Set('InventoryRequest', reverse='inventory_item')
    connectivity = Set('Connectivity', reverse='inventory_item')  # Added reverse relationship
    status = Required(str)  # Active, Inactive, Under Maintenance, etc.
    created_at = Required(datetime, default=datetime.utcnow)
    updated_at = Required(datetime)
    created_by = Required('User', reverse='inventory_items')


class CalibrationSchedule(db.Entity):
    """Calibration schedule for items"""
    _table_ = ("inventoryv1", "calibration_schedules")
    id = PrimaryKey(int, auto=True)
    inventory_item = Required(InventoryItem, reverse='calibrations')
    calibration_type = Required(str)
    frequency_days = Required(int)
    last_calibration = Optional(datetime)
    next_calibration = Required(datetime)
    remarks = Optional(str)
    created_at = Required(datetime, default=datetime.utcnow)
    updated_at = Required(datetime)
    created_by = Required('User', reverse='calibration_schedules')
    calibration_history = Set('CalibrationHistory', reverse='calibration_schedule')
    notification = Set('InstrumentCalibrationLog')


class CalibrationHistory(db.Entity):
    """History of calibrations performed"""
    _table_ = ("inventoryv1", "calibration_history")
    id = PrimaryKey(int, auto=True)
    calibration_schedule = Required(CalibrationSchedule, reverse='calibration_history')
    calibration_date = Required(datetime)
    performed_by = Required('User', reverse='calibration_histories')
    result = Required(str)  # Pass/Fail
    certificate_number = Optional(str)
    remarks = Optional(str)
    next_due_date = Required(datetime)
    created_at = Required(datetime, default=datetime.utcnow)


class InventoryRequest(db.Entity):
    """Request for inventory items"""
    _table_ = ("inventoryv1", "requests")
    id = PrimaryKey(int, auto=True)
    inventory_item = Required(InventoryItem, reverse='requests')
    requested_by = Required('User', reverse='inventory_requests')
    order = Required('Order', reverse='inventory_requests')  # Link to Master Order
    operation = Optional('Operation', reverse='inventory_requests')  # Link to specific operation if applicable
    quantity = Required(int)
    purpose = Required(str)
    status = Required(str)  # Pending, Approved, Rejected, Returned
    approved_by = Optional('User', reverse='approved_inventory_requests')
    approved_at = Optional(datetime)
    expected_return_date = Required(datetime)
    actual_return_date = Optional(datetime)
    remarks = Optional(str)
    created_at = Required(datetime, default=datetime.utcnow)
    updated_at = Required(datetime)
    transactions = Set('InventoryTransaction', reverse='reference_request')


class InventoryTransaction(db.Entity):
    """Track all inventory movements"""
    _table_ = ("inventoryv1", "transactions")
    id = PrimaryKey(int, auto=True)
    inventory_item = Required(InventoryItem, reverse='transactions')
    transaction_type = Required(str)  # Issue, Return, Maintenance, etc.
    quantity = Required(int)
    reference_request = Optional(InventoryRequest, reverse='transactions')
    performed_by = Required('User', reverse='inventory_transactions')
    remarks = Optional(str)
    created_at = Required(datetime, default=datetime.utcnow)