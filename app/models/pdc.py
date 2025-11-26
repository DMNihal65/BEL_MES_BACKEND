from datetime import datetime
from pony.orm import *
from ..database.connection import db
from .master_order import Order


class PDC(db.Entity):
    _table_ = ("scheduling", "pdc")
    id = PrimaryKey(int, auto=True)
    order_id = Required(Order, reverse='pdc_records')
    part_number = Required(str)
    production_order = Required(str)
    pdc_data = Required(datetime)
    data_source = Required(str)
    created_at = Required(datetime, default=datetime.utcnow)
    updated_at = Required(datetime, default=datetime.utcnow)
    is_active = Required(bool, default=True)