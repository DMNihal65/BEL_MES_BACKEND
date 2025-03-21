from datetime import datetime
from pony.orm import *
from . import User, Order, Operation, Machine
from ..database.connection import db

class PartScheduleStatus(db.Entity):
    """Controls which parts are active for scheduling"""
    _table_ = ("scheduling", "part_schedule_status")
    id = PrimaryKey(int, auto=True)
    part_number = Required(str, unique=True)
    status = Required(str, default='inactive')  # 'active' or 'inactive'
    created_at = Required(datetime, default=datetime.utcnow)
    updated_at = Required(datetime, default=datetime.utcnow)

    def before_update(self):
        self.updated_at = datetime.utcnow()

class PlannedScheduleItem(db.Entity):
    """Stores the actual schedule results"""
    _table_ = ("scheduling", "planned_schedule_items")
    id = PrimaryKey(int, auto=True)
    order = Required(Order, reverse='planned_schedule_items')  # Added reverse
    operation = Required(Operation, reverse='planned_schedule_items')  # Added reverse
    machine = Required(Machine, reverse='planned_schedule_items')  # Added reverse
    initial_start_time = Required(datetime)
    initial_end_time = Required(datetime)
    total_quantity = Required(int)
    remaining_quantity = Required(int)
    status = Optional(str)
    current_version = Optional(int)
    created_at = Required(datetime, default=datetime.utcnow)
    schedule_versions = Set('ScheduleVersion')

class ScheduleVersion(db.Entity):
    """Tracks different versions of schedules"""
    _table_ = ("scheduling", "schedule_versions")
    id = PrimaryKey(int, auto=True)
    schedule_item = Required(PlannedScheduleItem)
    version_number = Required(int)
    planned_start_time = Required(datetime)
    planned_end_time = Required(datetime)
    planned_quantity = Required(int)
    completed_quantity = Required(int, default=0)
    remaining_quantity = Required(int)
    is_active = Required(bool, default=True)
    created_at = Required(datetime, default=datetime.utcnow)
    reschedule_histories_as_current = Set('RescheduleHistory', reverse='schedule_version')
    reschedule_histories_as_previous = Set('RescheduleHistory', reverse='previous_version')
    production_logs = Set('ProductionLog')

class RescheduleHistory(db.Entity):
    """Tracks schedule changes"""
    _table_ = ("scheduling", "reschedule_history")
    id = PrimaryKey(int, auto=True)
    schedule_version = Required(ScheduleVersion, reverse='reschedule_histories_as_current')
    previous_version = Required(ScheduleVersion, reverse='reschedule_histories_as_previous')
    reason = Required(str)
    rescheduled_by_operator = Required(User)
    rescheduled_at = Required(datetime, default=datetime.utcnow)
    old_start_time = Required(datetime)
    old_end_time = Required(datetime)
    new_start_time = Required(datetime)
    new_end_time = Required(datetime)

class ProductionLog(db.Entity):
    """Tracks production progress"""
    _table_ = ("scheduling", "production_logs")
    id = PrimaryKey(int, auto=True)
    machine_id = Optional(int)
    schedule_version = Optional(ScheduleVersion)
    operator = Optional(User)
    start_time = Optional(datetime)
    end_time = Optional(datetime)
    quantity_completed = Optional(int)
    quantity_rejected = Optional(int)
    notes = Optional(str)
