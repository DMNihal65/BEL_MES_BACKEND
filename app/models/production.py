from pony.orm import Required, Set, PrimaryKey, Optional
from datetime import datetime, time
from ..database.connection import db


class StatusLookup(db.Entity):
    """Entity class for status lookup table in production schema"""
    _table_ = ('production', 'status_lookup')

    status_id = PrimaryKey(int)
    status_name = Required(str, unique=True)
    machine_statuses = Set('MachineRaw')
    machine_statuses_live = Set('MachineRawLive')


class MachineRaw(db.Entity):
    """Entity class for machine_raw table in livedata schema"""
    _table_ = ('production', 'machine_raw')

    id = PrimaryKey(int, auto=True)  # Auto-incrementing ID
    machine_id = Required(int)  # Foreign key to master_order.machines table
    time_stamp = Required(datetime, default=lambda: datetime.utcnow())
    status = Required(StatusLookup)
    selected_program = Optional(str)
    active_program = Optional(str)
    program_number = Optional(str)
    part_count = Optional(int)
    job_in_progress = Optional(int)
    # PrimaryKey(id, machine_id)


class MachineRawLive(db.Entity):
    """Entity class for machine_raw table in livedata schema"""
    _table_ = ('production', 'machine_raw_live')

    machine_id = PrimaryKey(int)
    time_stamp = Required(datetime, default=lambda: datetime.utcnow())
    status = Required(StatusLookup)
    selected_program = Optional(str)
    active_program = Optional(str)
    program_number = Optional(str)
    part_count = Optional(int)
    job_status = Optional(int)
    job_in_progress = Optional(int)
