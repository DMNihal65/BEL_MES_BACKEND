from decimal import Decimal

from pony.orm import Required, Set, PrimaryKey, Optional, composite_key, select
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
    time_stamp = Required(datetime, default=lambda: datetime.utcnow())
    status = Required(StatusLookup)
    op_mode = Optional(int)
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
    selected_program = Optional(str)
    active_program = Optional(str)
    part_count = Optional(int)
    job_status = Optional(int)
    job_in_progress = Optional(int)

    def get_order_details(self):
        """Get associated order details through job_in_progress (operation_id)"""
        try:
            if self.job_in_progress:
                print(f"\nDEBUG: Looking up details for job_in_progress: {self.job_in_progress}")
                
                from app.models import PlannedScheduleItem, Operation, Order
                from pony.orm import select, desc

                # Try multiple ways to find the operation
                try:
                    # Method 1: Direct select query
                    operations = select(o for o in Operation if o.id == self.job_in_progress)[:]
                    print(f"DEBUG: Direct select found {len(operations)} operations")
                    
                    operation = None
                    if operations:
                        operation = operations[0]
                    else:
                        # Method 2: Try through PlannedScheduleItem
                        schedule_items = select(p for p in PlannedScheduleItem if p.operation.id == self.job_in_progress)[:]
                        print(f"DEBUG: Found {len(schedule_items)} schedule items")
                        
                        if schedule_items:
                            operation = schedule_items[0].operation
                        else:
                            # Method 3: Raw SQL equivalent query through Operation
                            sql_results = Operation.select_by_sql(
                                "SELECT * FROM master_order.operations WHERE id = $job_id",
                                {"job_id": self.job_in_progress}
                            )[:]
                            
                            if sql_results:
                                operation = sql_results[0]
                            
                    if operation:
                        print(f"DEBUG: Found operation: ID={operation.id}, Number={operation.operation_number}")
                        
                        # Get order details
                        order = operation.order
                        if order:
                            print(f"DEBUG: Found order: PO={order.production_order}, Part={order.part_number}")
                            
                            # Check for active schedule
                            schedule_items = select(p for p in PlannedScheduleItem 
                                               if p.operation.id == operation.id and 
                                               p.schedule_versions.filter(lambda v: v.is_active == True))[:]
                            
                            if schedule_items:
                                print(f"DEBUG: Found active schedule for operation")
                            else:
                                print(f"DEBUG: No active schedule found, using direct operation details")
                            
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
                            print(f"DEBUG: Operation found but no associated order")
                    else:
                        print(f"DEBUG: Could not find operation with any method")
                
                except Exception as inner_e:
                    print(f"DEBUG: Error during operation lookup: {str(inner_e)}")
                    import traceback
                    print(traceback.format_exc())
                
            return None
            
        except Exception as e:
            print(f"DEBUG: Error getting order details: {str(e)}")
            import traceback
            print("DEBUG: Full traceback:")
            print(traceback.format_exc())
            return None


class ShiftSummary(db.Entity):
    """Shift-wise production summary"""
    _table_ = ('production', 'shift_summary')

    id = PrimaryKey(int, auto=True)
    machine_id = Required(int)
    shift = Required(int)
    timestamp = Required(datetime)

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

    updatedate = Required(datetime, default=lambda: datetime.utcnow(), auto=True)


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
