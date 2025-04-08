from pony.orm import Required, Set, PrimaryKey, Optional, select
from datetime import datetime, time
from ..database.connection import db
from .master_order import Operation, Order  # Add these imports


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
    timestamp = Required(datetime, default=lambda: datetime.utcnow())
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
    timestamp = Required(datetime, default=lambda: datetime.utcnow())
    status = Required(StatusLookup)
    selected_program = Optional(str)
    active_program = Optional(str)
    # program_number = Optional(str)
    part_count = Optional(int)
    job_status = Optional(int)
    job_in_progress = Optional(int)

    def get_order_details(self):
        """Get associated order details through job_in_progress (operation_id)"""
        try:
            if self.job_in_progress:
                operation = Operation.get(id=self.job_in_progress)
                if operation:
                    order = operation.order
                    return {
                        'production_order': order.production_order,
                        'part_number': order.part_number,
                        'part_description': order.part_description,
                        'required_quantity': order.required_quantity,
                        'launched_quantity': order.launched_quantity,
                        'operation_number': operation.operation_number,
                        'operation_description': operation.operation_description
                    }
            return None
        except Exception as e:
            print(f"Error getting order details: {str(e)}")
            return None


class MachineDowntimes(db.Entity):
    _table_ = ('production', 'machine_downtimes')

    id = PrimaryKey(int, auto=True)
    machine_id = Required(int)
    status = Required(int)
    priority = Optional(int)
    type = Optional(str)
    description = Optional(str)
    open_dt = Required(datetime)
    inprogress_dt = Optional(datetime)
    closed_dt = Optional(datetime)