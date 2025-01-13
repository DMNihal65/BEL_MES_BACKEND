from sqlalchemy import (
    Column, Integer, Text, Date, Boolean, ForeignKey, Double, create_engine, text, String, TIMESTAMP, Float, JSON
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from Database.db_setup import engine, Base

# Define the list of schemas
SCHEMAS = ["master_order"]

# --------------------------------------
# Schema: master_order
# --------------------------------------
class WorkCenter(Base):
    __tablename__ = 'work_centers'
    __table_args__ = {"schema": "master_order"}

    id = Column(Integer, primary_key=True)
    code = Column(String)
    plant_id = Column(Integer)
    description = Column(String)
    operation = Column(String)

    # Relationships
    machines = relationship("Machine", back_populates="work_center")
    operations = relationship("Operation", back_populates="work_center")


class Machine(Base):
    __tablename__ = 'machines'
    __table_args__ = {"schema": "master_order"}

    id = Column(Integer, primary_key=True)
    work_center_id = Column(Integer, ForeignKey('master_order.work_centers.id'))
    type = Column(String)
    make = Column(String)
    model = Column(String)
    year_of_installation = Column(Integer)
    cnc_controller = Column(String)
    cnc_controller_series = Column(String)
    remarks = Column(String)
    calibration_date = Column(TIMESTAMP(timezone=False))
    last_maintenance_date = Column(TIMESTAMP(timezone=False))

    # Relationships
    work_center = relationship("WorkCenter", back_populates="machines")
    shifts = relationship("MachineShift", back_populates="machine")
    downtimes = relationship("MachineDowntime", back_populates="machine")
    statuses = relationship("MachineStatus", back_populates="machine")
    planned_schedule_items = relationship("PlannedScheduleItems", back_populates="machine")

class MachineShift(Base):
    __tablename__ = 'machine_shifts'
    __table_args__ = {"schema": "master_order"}

    id = Column(Integer, primary_key=True)
    machine_id = Column(Integer, ForeignKey('master_order.machines.id'))
    shift_start = Column(TIMESTAMP(timezone=False))
    shift_end = Column(TIMESTAMP(timezone=False))
    is_active = Column(Boolean)

    # Relationships
    machine = relationship("Machine", back_populates="shifts")


class MachineDowntime(Base):
    __tablename__ = 'machine_downtimes'
    __table_args__ = {"schema": "master_order"}

    id = Column(Integer, primary_key=True)
    machine_id = Column(Integer, ForeignKey('master_order.machines.id'))
    start_time = Column(TIMESTAMP(timezone=False))
    end_time = Column(TIMESTAMP(timezone=False))
    is_active = Column(Boolean)

    # Relationships
    machine = relationship("Machine", back_populates="downtimes")
    priority_logs = relationship("PriorityChangeLogs", back_populates="downtime")


class Project(Base):
    __tablename__ = 'projects'
    __table_args__ = {"schema": "master_order"}

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    priority = Column(Integer, default=1)
    start_date = Column(TIMESTAMP(timezone=False), nullable=True)
    end_date = Column(TIMESTAMP(timezone=False), nullable=True)
    delivery_date = Column(TIMESTAMP(timezone=False), nullable=True)

    # Relationships
    orders = relationship("Order", back_populates="project")
    priority_change_logs = relationship("PriorityChangeLogs", back_populates="project")


class Order(Base):
    __tablename__ = 'orders'
    __table_args__ = {"schema": "master_order"}

    id = Column(Integer, primary_key=True)
    production_order = Column(String, unique=True)
    sale_order = Column(String)
    wbs_element = Column(String)
    part_number = Column(String)
    part_description = Column(String)
    total_operations = Column(Integer)
    required_quantity = Column(Float)
    launched_quantity = Column(Float)
    raw_material = Column(String)
    plant_id = Column(Integer)
    project_id = Column(Integer, ForeignKey('master_order.projects.id'))

    # Relationships
    project = relationship("Project", back_populates="orders")
    operations = relationship("Operation", back_populates="order")
    tools = relationship("ToolList", back_populates="order")
    jigs = relationship("JigsAndFixturesList", back_populates="order")
    documents = relationship("Documents", back_populates="order")
    tool_usages = relationship("ToolUsage", back_populates="order")
    raw_materials = relationship("RawMaterial", back_populates="order")
    planned_schedule_items = relationship("PlannedScheduleItems", back_populates="order")  # Backreference

class Operation(Base):
    __tablename__ = 'operations'
    __table_args__ = {"schema": "master_order"}

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('master_order.orders.id'))
    operation_number = Column(Integer)
    work_center_id = Column(Integer, ForeignKey('master_order.work_centers.id'))
    operation_description = Column(String)
    setup_time = Column(Float)
    ideal_cycle_time = Column(Float)

    # Relationships
    order = relationship("Order", back_populates="operations")
    work_center = relationship("WorkCenter", back_populates="operations")
    process_plans = relationship("ProcessPlan", back_populates="operation")
    tools = relationship("ToolList", back_populates="operation")
    jigs = relationship("JigsAndFixturesList", back_populates="operation")
    programs = relationship("Program", back_populates="program")
    tool_usages = relationship("ToolUsage", back_populates="operation")
    planned_schedule_items = relationship("PlannedScheduleItems", back_populates="operation")  # Backreference

class ProcessPlan(Base):
    __tablename__ = 'process_plan'
    __table_args__ = {"schema": "master_order"}

    id = Column(Integer, primary_key=True)
    op_id = Column(Integer, ForeignKey('master_order.operations.id'))
    instructions = Column(String)
    images = Column(String)
    remarks = Column(String)

    # Relationships
    operation = relationship("Operation", back_populates="process_plans")


class Program(Base):
    __tablename__ = 'programs'
    __table_args__ = {"schema": "master_order"}

    id = Column(Integer, primary_key=True)
    op_id = Column(Integer, ForeignKey('master_order.operations.id'))
    program_name = Column(String)
    program_number = Column(String)
    version = Column(String)
    update_date = Column(TIMESTAMP(timezone=False))

    # Relationships
    program = relationship("Operation", back_populates="programs")

class ToolList(Base):
    __tablename__ = 'tool_list'
    __table_args__ = {"schema": "master_order"}

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('master_order.orders.id'))
    op_id = Column(Integer, ForeignKey('master_order.operations.id'))
    tool_id = Column(Integer, ForeignKey('inventory.tools.id'))

    # Relationships
    order = relationship("Order", back_populates="tools")
    operation = relationship("Operation", back_populates="tools")
    tool = relationship("Tool", back_populates="tool_lists")


class JigsAndFixturesList(Base):
    __tablename__ = 'jigs_and_fixtures_list'
    __table_args__ = {"schema": "master_order"}

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('master_order.orders.id'))
    op_id = Column(Integer, ForeignKey('master_order.operations.id'))
    jigs_id = Column(Integer, ForeignKey('inventory.jigs_fixtures.id'))

    # Relationships
    order = relationship("Order", back_populates="jigs")
    operation = relationship("Operation", back_populates="jigs")
    jig_fixture = relationship("JigsFixture", back_populates="jigs_lists")


class Status(Base):
    __tablename__ = 'status'
    __table_args__ = {"schema": "master_order"}

    id = Column(Integer, primary_key=True)
    name = Column(String)
    description = Column(String)

    # Relationships
    machine_statuses = relationship("MachineStatus", back_populates="status")

class MachineStatus(Base):
    __tablename__ = 'machine_status'
    __table_args__ = {"schema": "master_order"}

    id = Column(Integer, primary_key=True)
    machine_id = Column(Integer, ForeignKey('master_order.machines.id'))
    status_id = Column(Integer, ForeignKey('master_order.status.id'))
    description = Column(String)

    # Relationships
    machine = relationship("Machine", back_populates="statuses")
    status = relationship("Status", back_populates="machine_statuses")


class Documents(Base):
    __tablename__ = 'documents'
    __table_args__ = {"schema": "master_order"}

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('master_order.orders.id'))
    document_name = Column(String)
    type = Column(String)
    upload_date = Column(TIMESTAMP)
    revision_date = Column(TIMESTAMP)
    version = Column(String)

    # Relationships
    order = relationship("Order", back_populates="documents")

class UserRole(Base):
    __tablename__ = 'user_role'
    __table_args__ = {"schema": "master_order"}

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    access = Column(String, nullable=False)

    # Relationships
    users = relationship("User", back_populates="role")

class User(Base):
    __tablename__ = 'user'
    __table_args__ = {"schema": "master_order"}

    id = Column(Integer, primary_key=True)
    role_id = Column(Integer, ForeignKey('master_order.user_role.id'), nullable=False)
    user_name = Column(String, unique=True)
    password = Column(String)

    # Relationships
    role = relationship("UserRole", back_populates="users")
    logs = relationship("UserLogs", back_populates="user")
    production_logs = relationship("ProductionLogs", back_populates="operator")  # Backreference
    priority_change_logs = relationship("PriorityChangeLogs", back_populates="operator")  # Backreference
    tool_usages = relationship("ToolUsage", back_populates="operator")

class UserLogs(Base):
    __tablename__ = 'user_logs'
    __table_args__ = {"schema": "master_order"}

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('master_order.user.id'))
    login_timestamp = Column(TIMESTAMP)
    logout_timestamp = Column(TIMESTAMP)

    # Relationships
    user = relationship("User", back_populates="logs")

class MPP(Base):
    __tablename__ = 'mpp'
    __table_args__ = {"schema": "master_order"}

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('master_order.orders.id'))
    operation_id = Column(Integer, ForeignKey('master_order.operations.id'))
    document_id = Column(Integer, ForeignKey('master_order.documents.id'), nullable=True)

    # Fixture and IPID Details
    fixture_number = Column(String)
    ipid_number = Column(String)

    # Datum Information
    datum_x = Column(String)
    datum_y = Column(String)
    datum_z = Column(String)

    # Work Holding Instructions stored as JSON
    # Format: {
    #   "sections": [
    #     {"title": "section_title", "instructions": "instruction_text", "sequence": 1},
    #     ...
    #   ]
    # }
    work_instructions = Column(JSON, default={"sections": []})

    # Relationships
    order = relationship("Order")
    operation = relationship("Operation")
    document = relationship("Documents")
