from datetime import datetime
from pony.orm import *
from decimal import Decimal
from ..database.connection import db  # Import the shared db instance

class InventoryStatus(db.Entity):
    _table_ = ("inventory", "inventory_status")
    id = PrimaryKey(int, auto=True)
    name = Required(str)
    description = Optional(str)
    raw_materials = Set('RawMaterial')
    instruments = Set('Instrument')
    tools = Set('Tool')
    jigs_fixtures = Set('JigsFixture')  # Add this line


class ToolType(db.Entity):
    _table_ = ("inventory", "tool_types")
    id = PrimaryKey(int, auto=True)
    name = Required(str)
    description = Optional(str)
    tools = Set('Tool')

class Tool(db.Entity):
    _table_ = ("inventory", "tools")
    id = PrimaryKey(int, auto=True)
    type = Required(ToolType)
    description = Optional(str)
    hsl_part_number = Optional(str)
    quantity = Required(float)
    status = Required(InventoryStatus)
    tool_usage = Set('ToolUsage')

class ToolUsage(db.Entity):
    _table_ = ("inventory", "tool_usage")
    id = PrimaryKey(int, auto=True)
    tool = Required(Tool)
    order_id = Required(int, column='order_id')  # Changed to direct foreign key reference
    operator_id = Required(int, column='operator_id')  # Changed to direct foreign key reference
    op_id = Required(int, column='op_id')  # Changed to direct foreign key reference
    quantity = Required(float)

class InstrumentType(db.Entity):
    _table_ = ("inventory", "instrument_types")
    id = PrimaryKey(int, auto=True)
    name = Required(str)
    description = Optional(str)
    instruments = Set('Instrument')

class Instrument(db.Entity):
    _table_ = ("inventory", "instruments")
    id = PrimaryKey(int, auto=True)
    type = Required(InstrumentType)
    description = Optional(str)
    instrument_code = Optional(str)
    size = Optional(str)
    equipment_number = Optional(str)
    maintenance_plan = Optional(str)
    notification_number = Optional(str)
    calibration_date = Optional(datetime)
    calibration_due_date = Optional(datetime)
    location = Optional(str)
    quantity = Required(float)
    status = Required(InventoryStatus)

class JigsFixture(db.Entity):
    _table_ = ("inventory", "jigs_fixtures")
    id = PrimaryKey(int, auto=True)
    project_name = Required(str)
    part_number = Required(str)
    revision = Optional(str)
    description = Optional(str)
    operation_number = Required(int)
    fixture_number = Required(str)
    status = Required(InventoryStatus)

class Unit(db.Entity):
    _table_ = ("inventory", "units")
    id = PrimaryKey(int, auto=True)
    name = Required(str)
    raw_materials = Set('RawMaterial')
    spares_consumables = Set('SparesConsumable')

class RawMaterial(db.Entity):
    _table_ = ("inventory", "raw_materials")
    id = PrimaryKey(int, auto=True)
    orders = Set('Order')  # Reverse relationship
    child_part_number = Required(str)
    description = Optional(str)
    quantity = Required(float)
    unit = Required('Unit')
    status = Required('InventoryStatus')
    available_from = Optional(datetime)


class SparesConsumable(db.Entity):
    _table_ = ("inventory", "spares_consumables")
    id = PrimaryKey(int, auto=True)
    description = Required(str)
    unit = Required(Unit)
    quantity = Required(float)