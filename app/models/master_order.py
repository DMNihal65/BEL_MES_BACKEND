from datetime import datetime
from pony.orm import *
from decimal import Decimal

from app.models.user import User
from ..database.connection import db  # Import the shared db instance


class WorkCenter(db.Entity):
    _table_ = ("master_order", "WorkCenter")
    id = PrimaryKey(int, auto=True)
    code = Required(str)
    plant_id = Required(str)
    work_center_name = Optional(str)  # Renamed from 'operation'
    description = Optional(str)
    machines = Set('Machine')
    operations = Set('Operation')


class Machine(db.Entity):
    _table_ = ("master_order", "machines")
    id = PrimaryKey(int, auto=True)
    work_center = Required(WorkCenter)
    type = Required(str)
    make = Required(str)
    model = Required(str)
    year_of_installation = Optional(int)
    cnc_controller = Optional(str)
    cnc_controller_series = Optional(str)
    remarks = Optional(str)
    calibration_date = Optional(datetime)
    calibration_due_date = Optional(datetime)  # Added this field
    last_maintenance_date = Optional(datetime)
    shifts = Set('MachineShift')
    downtimes = Set('MachineDowntime')
    status = Set('MachineStatus')
    operations = Set('Operation')  # Reverse relationship
    planned_schedule_items = Set('PlannedScheduleItem', reverse='machine')

class MachineShift(db.Entity):
    _table_ = ("master_order", "machine_shifts")
    id = PrimaryKey(int, auto=True)
    machine = Required(Machine)
    shift_start = Required(datetime)
    shift_end = Required(datetime)
    is_active = Required(bool, default=True)


class MachineDowntime(db.Entity):
    _table_ = ("master_order", "machine_downtimes")
    id = PrimaryKey(int, auto=True)
    machine = Required(Machine)
    start_time = Required(datetime)
    end_time = Required(datetime)
    is_active = Required(bool, default=True)


class Status(db.Entity):
    _table_ = ("master_order", "status")
    id = PrimaryKey(int, auto=True)
    name = Required(str)
    description = Optional(str)
    machine_statuses = Set('MachineStatus', reverse='status')


class MachineStatus(db.Entity):
    _table_ = ("master_order", "machine_status")
    id = PrimaryKey(int, auto=True)
    machine = Required(Machine)
    status = Required(Status)
    description = Optional(str)
    available_from = Optional(datetime)  # New column


class Project(db.Entity):
    _table_ = ("master_order", "projects")
    id = PrimaryKey(int, auto=True)
    name = Required(str)
    priority = Required(int)
    start_date = Required(datetime)
    end_date = Required(datetime)
    delivery_date = Required(datetime)  # New column
    orders = Set('Order')


class Order(db.Entity):
    _table_ = ("master_order", "orders")
    id = PrimaryKey(int, auto=True)
    production_order = Required(str, unique=True)
    sale_order = Optional(str)
    wbs_element = Optional(str)
    part_number = Required(str)
    part_description = Optional(str)
    total_operations = Required(int)
    required_quantity = Required(int)
    launched_quantity = Required(int)
    raw_material = Required('RawMaterial')  # Linked to RawMaterial table
    plant_id = Required(str)
    project = Required('Project')  # ProjectID linked here
    operations = Set('Operation')
    documents = Set('Document', reverse='part_number_id')  # Match the field name in Document
    tools = Set('ToolList')
    jigs_fixtures = Set('JigsAndFixturesList')
    mpps = Set('MPP', reverse='order')  # Add this line for MPP relationship
    planned_schedule_items = Set('PlannedScheduleItem', reverse='order')
    inventory_requests = Set("InventoryRequest")
    documents_v2 = Set('DocumentV2', reverse='production_order')
    order_tools = Set("OrderTool", reverse="order")  # Updated relationship name


class Operation(db.Entity):
    _table_ = ("master_order", "operations")
    id = PrimaryKey(int, auto=True)
    order = Required(Order)
    operation_number = Required(int)
    work_center = Required(WorkCenter)
    machine = Required('Machine')
    operation_description = Optional(str)
    setup_time = Required(Decimal)
    ideal_cycle_time = Required(Decimal)
    process_plans = Set('ProcessPlan')
    tools = Set('ToolList')
    jigs_fixtures = Set('JigsAndFixturesList')
    programs = Set('Program')
    mpps = Set('MPP', reverse='operation')
    planned_schedule_items = Set('PlannedScheduleItem', reverse='operation')
    order_tools = Set('OrderTool', reverse='operation')


    inventory_requests = Set("InventoryRequest")


class ProcessPlan(db.Entity):
    _table_ = ("master_order", "process_plan")
    id = PrimaryKey(int, auto=True)
    operation = Required(Operation)
    instructions = Optional(str)
    images = Optional(str)
    remarks = Optional(str)
    # program = Optional('Program', reverse='process_plan')


class Program(db.Entity):
    _table_ = ("master_order", "programs")
    id = PrimaryKey(int, auto=True)
    operation = Required(Operation)
    program_name = Required(str)
    program_number = Required(str)
    version = Required(str)
    update_date = Required(datetime)


class OrderTool(db.Entity):
    """
    Table for storing tools used in orders.
    """
    _table_ = ("master_order", "order_tools")  # Changed table name to force new creation
    id = PrimaryKey(int, auto=True)
    order = Required("Order", reverse="order_tools")
    operation = Optional("Operation", reverse="order_tools")
    tool_name = Required(str)
    tool_number = Required(str)
    bel_partnumber = Optional(str)
    description = Optional(str)
    quantity = Required(int, default=1)
    created_at = Required(datetime, default=datetime.now)
    updated_at = Required(datetime, default=datetime.now)

    def before_update(self):
        self.updated_at = datetime.now()


# class Document(db.Entity):
#     _table_ = ("master_order", "documents")
#     id = PrimaryKey(int, auto=True)
#     order = Required(Order)
#     document_name = Required(str)
#     type = Required(str)
#     upload_date = Required(datetime)
#     revision_date = Optional(datetime)
#     version = Required(str)
#     mpps = Set('MPP', reverse='document')

class ToolList(db.Entity):
    _table_ = ("master_order", "tool_list")
    id = PrimaryKey(int, auto=True)
    order = Required(Order)
    operation = Required(Operation)
    tool_id = Required(str)


class JigsAndFixturesList(db.Entity):
    _table_ = ("master_order", "jigs_and_fixtures_list")
    id = PrimaryKey(int, auto=True)
    order = Required(Order)
    operation = Required(Operation)
    jigs_id = Required(str)


class UserLogs(db.Entity):
    _table_ = ("master_order", "user_logs")
    id = PrimaryKey(int, auto=True)
    user = Required('User', reverse='user_logs')  # Update to include proper reverse reference
    login_timestamp = Required(datetime)
    logout_timestamp = Optional(datetime)


class MPP(db.Entity):
    _table_ = ("master_order", "mpp")
    id = PrimaryKey(int, auto=True)
    order = Required(Order, reverse='mpps')
    operation = Required(Operation, reverse='mpps')
    document = Optional('Document', reverse='mpps')  # Use string reference
    fixture_number = Optional(str)
    ipid_number = Optional(str)
    datum_x = Optional(str)
    datum_y = Optional(str)
    datum_z = Optional(str)
    work_instructions = Required(Json, default={"sections": []})
