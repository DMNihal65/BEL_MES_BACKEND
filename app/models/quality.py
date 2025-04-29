from pony.orm import *
from ..database.connection import db  # Import the shared db instance
from datetime import datetime
from .master_order import Order
from .document_management_v2 import DocumentV2

class MasterBoc(db.Entity):
    """
    Master BOC table for storing bill of characteristics data
    """
    _table_ = ("quality", "master_boc")  # (schema_name, table_name)

    id = PrimaryKey(int, auto=True)
    order = Required(Order, column='order_id', reverse='master_bocs')  # Added reverse relationship
    document = Required(DocumentV2, column='document_id', reverse='master_bocs')  # Added reverse relationship
    nominal = Required(str)
    uppertol = Required(float)
    lowertol = Required(float)
    zone = Required(str)
    dimension_type = Required(str)
    measured_instrument = Required(str)
    op_no = Required(int)
    bbox = Required(str)  # Storing as JSON string or specific format
    ipid = Required(str)  # Added new field
    created_at = Required(datetime, default=lambda: datetime.now())

class StageInspection(db.Entity):
    """
    Stage Inspection table for storing inspection measurements
    """
    _table_ = ("quality", "stage_inspection")

    id = PrimaryKey(int, auto=True)
    op_id = Required(int)
    nominal_value = Required(str)
    uppertol = Required(float)
    lowertol = Required(float)
    zone = Required(str)
    dimension_type = Required(str)
    measured_1 = Required(float)
    measured_2 = Required(float)
    measured_3 = Required(float)
    measured_mean = Required(float)
    measured_instrument = Required(str)
    op_no = Required(int)
    order_id = Required(int)
    quantity_no = Optional(int)  # Change from Required to Optional
    is_done = Required(bool, default=False)  # Added is_done field
    created_at = Required(datetime, default=lambda: datetime.now())

class Connectivity(db.Entity):
    """
    Connectivity table for storing instrument connectivity information
    """
    _table_ = ("quality", "connectivity")

    id = PrimaryKey(int, auto=True)
    inventory_item = Required('InventoryItem', reverse='connectivity')
    instrument = Required(str)
    uuid = Required(str)
    address = Required(str)  # Added address field
    created_at = Required(datetime, default=lambda: datetime.now())

class FTP(db.Entity):
    """
    FTP table for tracking IPID completion status
    """
    _table_ = ("quality", "ftp_status")  # Changed table name to be more specific

    id = PrimaryKey(int, auto=True)
    order_id = Required(int, size=64)  # Added size specification
    ipid = Required(str, max_len=255)  # Added max length
    is_completed = Required(bool, default=False)
    created_at = Required(datetime, default=lambda: datetime.now())
    updated_at = Required(datetime, default=lambda: datetime.now())

    composite_key(order_id, ipid)  # Ensure unique combination of order_id and ipid