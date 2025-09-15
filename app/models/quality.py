from pony.orm import *
from ..database.connection import db  # Import the shared db instance
from datetime import datetime
from .master_order import Order  # Import Order for foreign key relationship
# from .document_management_v2 import DocumentV2, DocumentTypeV2


class MasterBoc(db.Entity):
    """
    Master BOC table for storing bill of characteristics data
    """
    _table_ = ("quality", "master_boc")  # (schema_name, table_name)

    id = PrimaryKey(int, auto=True)
    part_number = Required(str)  # Part number string
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


# class PartNumberIPID(db.Entity):
#     _table_ = ("quality", "pn_ipid")

#     id = PrimaryKey(int, auto=True)
#     part_number = Required(str)
#     op_no = Required(int)
#     ipid = Required(str)
#     created_at = Required(datetime, default=lambda: datetime.now())

#     # Enforce unique constraint
#     composite_index = (part_number, op_no)
#     # master_bocs_pn = Set('MasterBocPN', reverse='pn_ipid')

# class MasterBocPN(db.Entity):
#     """
#     Master BOC table for storing bill of characteristics data
#     """
#     _table_ = ("quality", "master_boc_pn")  # (schema_name, table_name)

#     id = PrimaryKey(int, auto=True)
#     # pn_ipid = Required(PartNumberIPID, reverse='master_bocs_pn')  # FK here
#     part_number = Required(str)
#     # document = Optional(DocumentTypeV2, column='document_id', reverse='master_bocs_pn')
#   # Changed to reference DocumentTypeV2
#     nominal = Required(str)
#     uppertol = Required(float)
#     lowertol = Required(float)
#     zone = Required(str)
#     dimension_type = Required(str)
#     measured_instrument = Required(str)
#     op_no = Required(int)
#     bbox = Required(str)  # Storing as JSON string or specific format
#     ipid = Required(str)  # Added new field
#     created_at = Required(datetime, default=lambda: datetime.now())


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
    measured_1 = Required(str)
    measured_2 = Required(str)
    measured_3 = Required(str)
    measured_mean = Required(str)
    measured_instrument = Required(str)
    used_inst = Required(str)  # Added new column
    op_no = Required(int)
    order_id = Required(int)
    quantity_no = Optional(int)  # Change from Required to Optional
    bbox = Optional(str)  # Added bbox field - storing as JSON string like MasterBoc
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
    Status=Required(str, max_len=255)
    created_at = Required(datetime, default=lambda: datetime.now())
    updated_at = Required(datetime, default=lambda: datetime.now())

    composite_key(order_id, ipid)  # Ensure unique combination of order_id and ipid