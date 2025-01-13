from sqlalchemy import Column, Integer, String, ForeignKey, Boolean, Float, TIMESTAMP, text
from sqlalchemy.orm import relationship, declarative_base
from Database.db_setup import engine, Base

# Define the list of schemas
SCHEMAS = ["inventory"]

class InventoryStatus(Base):
    __tablename__ = 'inventory_status'
    __table_args__ = {"schema": "inventory"}

    id = Column(Integer, primary_key=True)
    name = Column(String)
    description = Column(String)

    raw_materials = relationship("RawMaterial", back_populates="status")  # Existing relationship
    instruments = relationship("Instrument", back_populates="status")

class ToolType(Base):
    __tablename__ = 'tool_types'
    __table_args__ = {"schema": "inventory"}

    id = Column(Integer, primary_key=True)
    name = Column(String)
    description = Column(String)

    # Relationships
    tools = relationship("Tool", back_populates="type")

class Tool(Base):
    __tablename__ = 'tools'
    __table_args__ = {"schema": "inventory"}

    id = Column(Integer, primary_key=True)
    type_id = Column(Integer, ForeignKey('inventory.tool_types.id'))
    description = Column(String)
    hsl_part_number = Column(String)
    quantity = Column(Float)
    status_id = Column(Integer, ForeignKey('inventory.inventory_status.id'))

    # Relationships
    type = relationship("ToolType", back_populates="tools")
    tool_usage = relationship("ToolUsage", back_populates="tool")
    tool_lists = relationship("ToolList", back_populates="tool")

class ToolUsage(Base):
    __tablename__ = 'tool_usage'
    __table_args__ = {"schema": "inventory"}

    id = Column(Integer, primary_key=True)
    tool_id = Column(Integer, ForeignKey('inventory.tools.id'))
    order_id = Column(Integer, ForeignKey('master_order.orders.id'))
    operator_id = Column(Integer, ForeignKey('master_order.user.id'))
    op_id = Column(Integer, ForeignKey('master_order.operations.id'))
    quantity = Column(Float)

    # Relationships
    tool = relationship("Tool", back_populates="tool_usage")
    # operator = relationship("User", back_populates="tool_usages")
    order = relationship("Order", back_populates="tool_usages")
    operation = relationship("Operation", back_populates="tool_usages")
    operator = relationship("User", back_populates="tool_usages")


class InstrumentType(Base):
    __tablename__ = 'instrument_types'
    __table_args__ = {"schema": "inventory"}

    id = Column(Integer, primary_key=True)
    name = Column(String)
    description = Column(String)

    # Relationships
    instruments = relationship("Instrument", back_populates="type")

class Instrument(Base):
    __tablename__ = 'instruments'
    __table_args__ = {"schema": "inventory"}

    id = Column(Integer, primary_key=True)
    type_id = Column(Integer, ForeignKey('inventory.instrument_types.id'))
    description = Column(String)
    instrument_code = Column(String)
    size = Column(String)
    equipment_number = Column(String)
    maintenance_plan = Column(String)
    notification_number = Column(String)
    calibration_date = Column(TIMESTAMP)
    calibration_due_date = Column(TIMESTAMP)
    location = Column(String)
    quantity = Column(Float)
    status_id = Column(Integer, ForeignKey('inventory.inventory_status.id'))

    # Relationships
    type = relationship("InstrumentType", back_populates="instruments")
    status = relationship("InventoryStatus", back_populates="instruments")

class JigsFixture(Base):
    __tablename__ = 'jigs_fixtures'
    __table_args__ = {"schema": "inventory"}

    id = Column(Integer, primary_key=True)
    project_name = Column(String)
    part_number = Column(String)
    revision = Column(String)
    description = Column(String)
    operation_number = Column(Integer)
    fixture_number = Column(String)
    status_id = Column(Integer, ForeignKey('inventory.inventory_status.id'))

    # Relationships
    jigs_lists = relationship("JigsAndFixturesList", back_populates="jig_fixture")

class Unit(Base):
    __tablename__ = 'units'
    __table_args__ = {"schema": "inventory"}

    id = Column(Integer, primary_key=True)
    name = Column(String)

    raw_materials = relationship("RawMaterial", back_populates="unit")
    spares_consumables = relationship("SparesConsumable", back_populates="unit")

class RawMaterial(Base):
    __tablename__ = 'raw_materials'
    __table_args__ = {"schema": "inventory"}

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('master_order.orders.id'))
    child_part_number = Column(String)
    description = Column(String)
    quantity = Column(Float)
    unit_id = Column(Integer, ForeignKey('inventory.units.id'))
    status_id = Column(Integer, ForeignKey('inventory.inventory_status.id'))
    available_from = Column(TIMESTAMP)

    # Relationships
    order = relationship("Order", back_populates="raw_materials")
    unit = relationship("Unit", back_populates="raw_materials")
    status = relationship("InventoryStatus", back_populates="raw_materials")

class SparesConsumable(Base):
    __tablename__ = 'spares_consumables'
    __table_args__ = {"schema": "inventory"}

    id = Column(Integer, primary_key=True)
    description = Column(String)
    unit_id = Column(Integer, ForeignKey('inventory.units.id'))
    quantity = Column(Float)

    # Relationships
    unit = relationship("Unit", back_populates="spares_consumables")
