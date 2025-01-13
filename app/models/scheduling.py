from datetime import datetime
from pony.orm import *
from ..core.database import db

class PlannedScheduleItem(db.Entity):
    _table_ = 'planned_schedule_items'
    id = PrimaryKey(int, auto=True)
    order = Required('Order')
    operation = Required('Operation', reverse='planned_schedule_items')
    machine = Required('Machine')
    initial_start_time = Required(datetime)
    initial_end_time = Required(datetime)
    total_quantity = Required(int)
    remaining_quantity = Required(int)
    status = Required(str)
    current_version = Required(int)
    schedule_versions = Set('ScheduleVersion')

class ScheduleVersion(db.Entity):
    _table_ = 'schedule_versions'
    id = PrimaryKey(int, auto=True)
    schedule_item = Required(PlannedScheduleItem)
    version_number = Required(int)
    planned_start_time = Required(datetime)
    planned_end_time = Required(datetime)
    planned_quantity = Required(int)
    completed_quantity = Required(int, default=0)
    remaining_quantity = Required(int)
    is_active = Required(bool, default=True)
    created_at = Required(datetime, default=datetime.now)
    production_logs = Set('ProductionLog')
    reschedule_history_current = Set('RescheduleHistory', reverse='schedule_version')
    reschedule_history_previous = Set('RescheduleHistory', reverse='previous_version')

class RescheduleHistory(db.Entity):
    _table_ = 'reschedule_history'
    id = PrimaryKey(int, auto=True)
    schedule_version = Required(ScheduleVersion, reverse='reschedule_history_current')
    previous_version = Optional(ScheduleVersion, reverse='reschedule_history_previous')
    reason = Required(str)
    rescheduled_by_operator_id = Required(str)
    rescheduled_at = Required(datetime)
    old_start_time = Required(datetime)
    old_end_time = Required(datetime)
    new_start_time = Required(datetime)
    new_end_time = Required(datetime)
