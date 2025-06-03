from decimal import Decimal

from pony.orm import Required, Set, PrimaryKey, Optional, composite_key, select
from datetime import datetime, time
from ..database.connection import db
from .master_order import Operation, Order, Program  # Add Program import
from .scheduled import PlannedScheduleItem


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

    scheduled_job = Optional(Operation, reverse='machine_raw_1')
    actual_job = Optional(Operation, reverse='machine_raw_2')


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
    program_number = Optional(int)
    scheduled_job = Optional(Operation, reverse='machine_raw_live_1')
    actual_job = Optional(Operation, reverse='machine_raw_live_2')

    def get_order_details(self):
        """Get associated order details through actual_job or scheduled_job relationships"""
        try:
            # First approach: Use actual_job if available
            if self.actual_job:
                try:
                    print(f"\n=== Debug: Using actual_job with ID {self.actual_job.id} ===")
                    operation = self.actual_job
                    if operation:
                        print(f"Found operation: ID={operation.id}, Number={operation.operation_number}")

                        # Get order from operation
                        order = operation.order
                        if order:
                            print(f"Found order: PO={order.production_order}, Part={order.part_number}")
                            return {
                                'production_order': order.production_order,
                                'part_number': order.part_number,
                                'part_description': order.part_description,
                                'required_quantity': order.required_quantity,
                                'launched_quantity': order.launched_quantity,
                                'operation_number': operation.operation_number,
                                'operation_description': operation.operation_description
                            }
                        else:
                            print(f"No order found for actual_job operation {operation.id}")
                    else:
                        print(f"Actual job reference exists but operation is None")
                except Exception as actual_job_error:
                    print(f"Error processing actual_job: {str(actual_job_error)}")
                    import traceback
                    print(traceback.format_exc())

            # Second approach: Use scheduled_job if available
            if self.scheduled_job:
                try:
                    print(f"\n=== Debug: Using scheduled_job with ID {self.scheduled_job.id} ===")
                    operation = self.scheduled_job
                    if operation:
                        print(f"Found operation: ID={operation.id}, Number={operation.operation_number}")

                        # Get order from operation
                        order = operation.order
                        if order:
                            print(f"Found order: PO={order.production_order}, Part={order.part_number}")
                            return {
                                'production_order': order.production_order,
                                'part_number': order.part_number,
                                'part_description': order.part_description,
                                'required_quantity': order.required_quantity,
                                'launched_quantity': order.launched_quantity,
                                'operation_number': operation.operation_number,
                                'operation_description': operation.operation_description
                            }
                        else:
                            print(f"No order found for scheduled_job operation {operation.id}")
                    else:
                        print(f"Scheduled job reference exists but operation is None")
                except Exception as scheduled_job_error:
                    print(f"Error processing scheduled_job: {str(scheduled_job_error)}")
                    import traceback
                    print(traceback.format_exc())

            # Third approach: Use job_in_progress to find scheduled item
            if self.job_in_progress:
                try:
                    print(f"\n=== Debug: Using job_in_progress ID {self.job_in_progress} ===")
                    # Get the schedule item directly by ID
                    schedule_item = PlannedScheduleItem.get(id=self.job_in_progress)
                    if schedule_item:
                        print(f"Found schedule item: ID={schedule_item.id}")
                        operation = schedule_item.operation
                        order = schedule_item.order

                        if operation:
                            print(f"Found operation: ID={operation.id}, Number={operation.operation_number}")
                        else:
                            print(f"No operation found for schedule item {self.job_in_progress}")

                        if order:
                            print(f"Found order: PO={order.production_order}, Part={order.part_number}")
                            return {
                                'production_order': order.production_order,
                                'part_number': order.part_number,
                                'part_description': order.part_description,
                                'required_quantity': order.required_quantity,
                                'launched_quantity': order.launched_quantity,
                                'operation_number': operation.operation_number if operation else None,
                                'operation_description': operation.operation_description if operation else None
                            }
                        else:
                            print(f"No order found for schedule item {self.job_in_progress}")
                    else:
                        print(f"No schedule item found with ID {self.job_in_progress}")
                except Exception as schedule_error:
                    print(f"Error finding schedule item: {str(schedule_error)}")
                    import traceback
                    print(traceback.format_exc())

            # No match found, return None
            print("No operation or order information found through any available method")
            return None

        except Exception as e:
            print(f"Error getting order details: {str(e)}")
            import traceback
            print(traceback.format_exc())
            return None


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
    # status = Required(int)
    priority = Optional(int)
    category = Optional(str, nullable=True)
    description = Optional(str, nullable=True)
    open_dt = Required(datetime)
    inprogress_dt = Optional(datetime)
    closed_dt = Optional(datetime)
    reported_by = Optional(int)
    action_taken = Optional(str, nullable=True)


class OEEIssue(db.Entity):
    _table_ = ('production', 'oee_issue')
    category = Required(str)
    description = Required(str)
    machine = Required(int)  # just an ID, not a foreign key
    timestamp = Required(datetime, default=datetime.utcnow)
    reported_by = Required(int)
