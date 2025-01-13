from datetime import datetime
from pony.orm import *
from decimal import Decimal

db = Database()

class WorkCenter(db.Entity):
    _table_ = ("master_order", "WorkCenter") 
    # _schema_ = "master_order"
    id = PrimaryKey(int, auto=True)
    code = Required(str)
    plant_id = Required(str)
    description = Optional(str)
    operation = Optional(str)
    machines = Set('Machine')
    operations = Set('Operation')

class Machine(db.Entity):
    # _table_ = 'machines'
    _table_ = ("master_order", "machines") 
    # _schema_ = "master_order"
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
    last_maintenance_date = Optional(datetime)
    shifts = Set('MachineShift')
    downtimes = Set('MachineDowntime')
    status = Set('MachineStatus')

class MachineShift(db.Entity):
    # _table_ = 'machine_shifts'
    _table_ = ("master_order", "machine_shifts") 
    # _schema_ = "master_order"
    id = PrimaryKey(int, auto=True)
    machine = Required(Machine)
    shift_start = Required(datetime)
    shift_end = Required(datetime)
    is_active = Required(bool, default=True)

class MachineDowntime(db.Entity):
    # _table_ = 'machine_downtimes'
    _table_ = ("master_order", "machine_downtimes") 
    # _schema_ = "master_order"
    id = PrimaryKey(int, auto=True)
    machine = Required(Machine)
    start_time = Required(datetime)
    end_time = Required(datetime)
    is_active = Required(bool, default=True)

class MachineStatus(db.Entity):
    # _table_ = 'machine_status'
    _table_ = ("master_order", "machine_status") 
    # _schema_ = "master_order"
    id = PrimaryKey(int, auto=True)
    machine = Required(Machine)
    status_id = Required(int)
    description = Optional(str)

class Project(db.Entity):
    # _table_ = 'projects'
    _table_ = ("master_order", "projects") 
    # _schema_ = "master_order"
    id = PrimaryKey(int, auto=True)
    name = Required(str)
    priority = Required(int)
    start_date = Required(datetime)
    end_date = Required(datetime)
    orders = Set('Order')

class Order(db.Entity):
    # _table_ = 'orders'
    _table_ = ("master_order", "orders") 
    # _schema_ = "master_order"
    id = PrimaryKey(int, auto=True)
    production_order = Required(str, unique=True)
    sale_order = Optional(str)
    wbs_element = Optional(str)
    part_number = Required(str)
    part_description = Optional(str)
    total_operations = Required(int)
    required_quantity = Required(int)
    launched_quantity = Required(int)
    raw_material = Optional(str)
    plant_id = Required(str)
    delivery_date = Required(datetime)
    project = Required(Project)
    operations = Set('Operation')
    documents = Set('Document')
    tools = Set('ToolList')
    jigs_fixtures = Set('JigsAndFixturesList')

class Operation(db.Entity):
    # _table_ = 'operations'
    _table_ = ("master_order", "operations") 
    # _schema_ = "master_order"
    id = PrimaryKey(int, auto=True)
    order = Required(Order)
    operation_number = Required(int)
    work_center = Required(WorkCenter)
    # machine = Optional('Machine')
    operation_description = Optional(str)
    setup_time = Required(Decimal)
    ideal_cycle_time = Required(Decimal)
    process_plans = Set('ProcessPlan')
    tools = Set('ToolList')
    jigs_fixtures = Set('JigsAndFixturesList')
    programs = Set('Program')

class ProcessPlan(db.Entity):
    # _table_ = 'process_plan'
    _table_ = ("master_order", "process_plan") 
    # _schema_ = "master_order"
    id = PrimaryKey(int, auto=True)
    operation = Required(Operation)
    instructions = Optional(str)
    images = Optional(str)
    remarks = Optional(str)
    program = Optional('Program', reverse='process_plan')

class Program(db.Entity):
    # _table_ = 'programs'
    _table_ = ("master_order", "programs") 
    # _schema_ = "master_order"
    id = PrimaryKey(int, auto=True)
    operation = Required(Operation)
    process_plan = Optional(ProcessPlan)
    program_name = Required(str)
    program_number = Required(str)
    version = Required(str)
    update_date = Required(datetime)

class Document(db.Entity):
    # _table_ = 'documents'
    _table_ = ("master_order", "documents") 
    # _schema_ = "master_order"
    id = PrimaryKey(int, auto=True)
    order = Required(Order)
    document_name = Required(str)
    type = Required(str)
    upload_date = Required(datetime)
    revision_date = Optional(datetime)
    version = Required(str)

class ToolList(db.Entity):
    # _table_ = 'tool_list'
    _table_ = ("master_order", "tool_list") 
    # _schema_ = "master_order"
    id = PrimaryKey(int, auto=True)
    order = Required(Order)
    operation = Required(Operation)
    tool_id = Required(str)

class JigsAndFixturesList(db.Entity):
    # _table_ = 'jigs_and_fixtures_list'
    _table_ = ("master_order", "jigs_and_fixtures_list") 
    # _schema_ = "master_order"
    id = PrimaryKey(int, auto=True)
    order = Required(Order)
    operation = Required(Operation)
    jigs_id = Required(str) 