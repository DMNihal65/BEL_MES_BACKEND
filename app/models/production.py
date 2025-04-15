from decimal import Decimal

from pony.orm import Required, Set, PrimaryKey, Optional, composite_key
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

    id = PrimaryKey(int, auto=True)
    machine_id = Required(int)
    timestamp = Required(datetime, default=lambda: datetime.utcnow())
    status = Required(StatusLookup)
    op_mode = Optional(int)
    prog_status = Optional(int)
    selected_program = Optional(str)
    active_program = Optional(str)
    program_number = Optional(str)
    part_count = Optional(int)
    job_in_progress = Optional(int)
    part_status = Optional(int)


class MachineRawLive(db.Entity):
    """Entity class for machine_raw table in livedata schema"""
    _table_ = ('production', 'machine_raw_live')

    machine_id = PrimaryKey(int)
    timestamp = Required(datetime, default=lambda: datetime.utcnow())
    status = Required(StatusLookup)
    op_mode = Optional(int)
    prog_status = Optional(int)
    selected_program = Optional(str)
    active_program = Optional(str)
    part_count = Optional(int)
    job_status = Optional(int)
    job_in_progress = Optional(int)


class ShiftSummary(db.Entity):
    """Shift-wise production summary"""
    _table_ = ('production', 'shift_summary')

    id = PrimaryKey(int, auto=True)
    machine_id = Required(int)
    shift = Required(int)
    timestamp = Required(datetime)

    updatedate = Required(datetime, default=lambda: datetime.utcnow(), auto=True)

    off_time = Optional(time)
    idle_time = Optional(time)
    production_time = Optional(time)

    total_parts = Optional(int)
    good_parts = Optional(int)
    bad_parts = Optional(int)

    availability = Optional(Decimal, precision=5, scale=2)
    performance = Optional(Decimal, precision=5, scale=2)
    quality = Optional(Decimal, precision=5, scale=2)

    availability_loss = Optional(Decimal, precision=5, scale=2)
    performance_loss = Optional(Decimal, precision=5, scale=2)
    quality_loss = Optional(Decimal, precision=5, scale=2)

    oee = Optional(Decimal, precision=5, scale=2)


class ShiftInfo(db.Entity):
    """Shift timing configuration"""
    _table_ = ('production', 'shift_info')

    id = PrimaryKey(int, auto=True)
    start_time = Required(time)
    end_time = Required(time)


class ConfigInfo(db.Entity):
    """Shift timing configuration"""
    _table_ = ('production', 'config_info')

    id = PrimaryKey(int, auto=True)
    machine_id = Required(int, unique=True)
    shift_duration = Required(int)
    planned_non_production_time = Required(int)
    planned_downtime = Required(int)
    updatedate = Required(datetime, default=lambda: datetime.utcnow(), auto=True)


class MachineDowntimes(db.Entity):
    _table_ = ('production', 'machine_downtimes')

    id = PrimaryKey(int, auto=True)
    machine_id = Required(int)
    priority = Optional(int)
    category = Optional(str, nullable=True)
    description = Optional(str, nullable=True)
    open_dt = Required(datetime)
    inprogress_dt = Optional(datetime)
    closed_dt = Optional(datetime)
    reported_by = Optional(int)
    action_taken = Optional(str, nullable=True)
