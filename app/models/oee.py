from pony.orm import Required, Set, PrimaryKey, Optional
from datetime import datetime, time
from ..database.connection import db


# Add this with your other Entity definitions (but before db.generate_mapping())
class DailyAvailability(db.Entity):
    _table_ = ('production', 'daily_availability')
    id = PrimaryKey(int, auto=True)
    date = Required(datetime)
    machine_id = Required(int)
    actual_production_minutes = Required(float)
    planned_production_minutes = Required(float)
    availability_percentage = Required(float)
    total_parts = Required(int)
    good_parts = Required(int)
    bad_parts = Required(int)
    performance = Required(float)
    quality = Required(float)
    oee = Required(float)
    availability_loss = Required(float)
    performance_loss = Required(float)
    quality_loss = Required(float)



# Entity definitions
class ShiftTiming(db.Entity):
    _table_ = ('production', 'shift_timing')
    shift_id = PrimaryKey(int)
    start_time = Required(time)
    end_time = Required(time)
    shift_availabilities = Set('ShiftAvailability')


class ShiftAvailability(db.Entity):
    _table_ = ('production', 'shift_availability')
    id = PrimaryKey(int, auto=True)
    date = Required(datetime)
    machine_id = Required(int)
    shift_id = Required(ShiftTiming)
    actual_production_minutes = Required(float)
    planned_production_minutes = Required(float, default=480.0)
    off_time = Required(float, default=0.0)
    idle_time = Required(float, default=0.0)
    theoretical_cycletime = Required(float, default=30.0)
    total_parts = Required(int, default=0)
    good_parts = Required(int, default=0)
    bad_parts = Required(int, default=0)
    availability_percentage = Required(float)
    performance = Required(float, default=0.0)
    quality = Required(float, default=0.0)
    oee = Required(float, default=0.0)
    availability_loss = Required(float, default=0.0)
    performance_loss = Required(float, default=0.0)
    quality_loss = Required(float, default=0.0)




# Entity classes remain the same
class StatusLookup(db.Entity):
    """Entity class for status lookup table in production schema"""
    _table_ = ('production', 'status_lookup')

    status_id = PrimaryKey(int)
    status_name = Required(str, unique=True)
    machine_statuses = Set('MachineRaw')


class MachineRaw(db.Entity):
    """Entity class for machine_raw table in production schema"""
    _table_ = ('production', 'machine_raw')

    id = Required(int)
    machine_id = Required(int)
    time_stamp = Required(datetime, default=lambda: datetime.utcnow())
    status = Required(StatusLookup)
    job_in_progress = Required(str)
    program_number = Required(str, default='0')
    part_count = Required(int, default=0)
    PrimaryKey(id, machine_id)






# Add these Entity classes before db.generate_mapping()
class WeeklyAvailability(db.Entity):
    _table_ = ('production', 'weekly_availability')
    id = PrimaryKey(int, auto=True)
    year = Required(int)
    week_number = Required(int)
    start_date = Required(datetime)
    end_date = Required(datetime)
    machine_id = Required(int)
    actual_production_minutes = Required(float)
    planned_production_minutes = Required(float, default=10080.0)  # 7 days * 1440 minutes
    availability_percentage = Required(float)
    total_parts = Required(int, default=0)
    good_parts = Required(int, default=0)
    bad_parts = Required(int, default=0)
    performance = Required(float, default=0.0)
    quality = Required(float, default=0.0)
    oee = Required(float, default=0.0)
    availability_loss = Required(float, default=0.0)
    performance_loss = Required(float, default=0.0)
    quality_loss = Required(float, default=0.0)

class MonthlyAvailability(db.Entity):
    _table_ = ('production', 'monthly_availability')
    id = PrimaryKey(int, auto=True)
    year = Required(int)
    month = Required(int)
    start_date = Required(datetime)
    end_date = Required(datetime)
    machine_id = Required(int)
    actual_production_minutes = Required(float)
    planned_production_minutes = Required(float)
    availability_percentage = Required(float)
    total_parts = Required(int, default=0)
    good_parts = Required(int, default=0)
    bad_parts = Required(int, default=0)
    performance = Required(float, default=0.0)
    quality = Required(float, default=0.0)
    oee = Required(float, default=0.0)
    availability_loss = Required(float, default=0.0)
    performance_loss = Required(float, default=0.0)
    quality_loss = Required(float, default=0.0)


