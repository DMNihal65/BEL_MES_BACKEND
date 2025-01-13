from sqlalchemy import (
    Column, Integer, String, ForeignKey, DateTime, Boolean, Float, Text, text
)
from sqlalchemy.orm import relationship
from Database.db_setup import engine, Base

# Define the list of schemas
SCHEMAS = ["planning_scheduling"]

# Table: planned_schedule_items
class PlannedScheduleItems(Base):
    __tablename__ = 'planned_schedule_items'
    __table_args__ = {"schema": "planning_scheduling"}

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('master_order.orders.id'), nullable=False)
    op_id = Column(Integer, ForeignKey('master_order.operations.id'), nullable=False)
    machine_id = Column(Integer, ForeignKey('master_order.machines.id'), nullable=False)
    initial_start_time = Column(DateTime, nullable=False)
    initial_end_time = Column(DateTime, nullable=False)
    total_quantity = Column(Integer, nullable=False)
    remaining_quantity = Column(Integer, nullable=False)
    status = Column(String(50), nullable=False)
    current_version = Column(Integer, nullable=False)

    # Relationships
    schedule_versions = relationship("ScheduleVersions", back_populates="planned_schedule_items")
    order = relationship("Order", back_populates="planned_schedule_items")
    operation = relationship("Operation", back_populates="planned_schedule_items")
    machine = relationship("Machine", back_populates="planned_schedule_items")

class ScheduleVersions(Base):
    __tablename__ = 'schedule_versions'
    __table_args__ = {"schema": "planning_scheduling"}

    id = Column(Integer, primary_key=True)
    schedule_item_id = Column(Integer, ForeignKey('planning_scheduling.planned_schedule_items.id'), nullable=False)
    version_number = Column(Integer, nullable=False)
    planned_start_time = Column(DateTime, nullable=False)
    planned_end_time = Column(DateTime, nullable=False)
    planned_quantity = Column(Integer, nullable=False)
    completed_quantity = Column(Integer, nullable=False)
    remaining_quantity = Column(Integer, nullable=False)
    is_active = Column(Boolean, nullable=False)
    created_at = Column(DateTime, nullable=False)

    # Relationships
    planned_schedule_items = relationship("PlannedScheduleItems", back_populates="schedule_versions")
    # reschedule_history = relationship("RescheduleHistory", back_populates="schedule_version")
    reschedule_history = relationship("RescheduleHistory",
                                      back_populates="schedule_version",
                                      foreign_keys="[RescheduleHistory.schedule_version_id]")
    production_logs = relationship("ProductionLogs", back_populates="schedule_version")

class RescheduleHistory(Base):
    __tablename__ = 'reschedule_history'
    __table_args__ = {"schema": "planning_scheduling"}

    id = Column(Integer, primary_key=True)
    schedule_version_id = Column(Integer, ForeignKey('planning_scheduling.schedule_versions.id'), nullable=False)
    previous_version_id = Column(Integer, ForeignKey('planning_scheduling.schedule_versions.id'), nullable=True)
    reason = Column(Text, nullable=False)
    rescheduled_by_operator_id = Column(Integer, nullable=False)
    rescheduled_at = Column(DateTime, nullable=False)
    old_start_time = Column(DateTime, nullable=False)
    old_end_time = Column(DateTime, nullable=False)
    new_start_time = Column(DateTime, nullable=False)
    new_end_time = Column(DateTime, nullable=False)

    # Relationships
    # schedule_version = relationship("ScheduleVersions", back_populates="reschedule_history", foreign_keys=[schedule_version_id])
    schedule_version = relationship("ScheduleVersions",
                                    back_populates="reschedule_history",
                                    foreign_keys=[schedule_version_id])
class ProductionLogs(Base):
    __tablename__ = 'production_logs'
    __table_args__ = {"schema": "planning_scheduling"}

    id = Column(Integer, primary_key=True)
    schedule_version_id = Column(Integer, ForeignKey('planning_scheduling.schedule_versions.id'), nullable=False)
    operator_id = Column(Integer, ForeignKey('master_order.user.id'), nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    quantity_completed = Column(Integer, nullable=False)
    quantity_rejected = Column(Integer, nullable=False)
    notes = Column(Text)

    # Relationships
    schedule_version = relationship("ScheduleVersions", back_populates="production_logs")
    operator = relationship("User", back_populates="production_logs")

class PriorityChangeLogs(Base):
    __tablename__ = 'priority_change_logs'
    __table_args__ = {"schema": "planning_scheduling"}

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('master_order.projects.id'), nullable=False)
    change_date_time = Column(DateTime, nullable=False)
    delivery_date = Column(DateTime, nullable=False)
    operator_id = Column(Integer, ForeignKey('master_order.user.id'), nullable=False)
    reason = Column(Text, nullable=False)
    reschedule_time = Column(DateTime, nullable=True)
    downtime_id = Column(Integer, ForeignKey('master_order.machine_downtimes.id'), nullable=True)

    # Relationships
    project = relationship("Project", back_populates="priority_change_logs")
    operator = relationship("User", back_populates="priority_change_logs")
    downtime = relationship("MachineDowntime", back_populates="priority_logs")