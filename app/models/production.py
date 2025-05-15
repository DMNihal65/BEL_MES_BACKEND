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
        """Get associated order details through job_in_progress (operation_id) or by matching program name"""
        try:
            # First approach: Use job_in_progress (operation_id) if available
            if self.job_in_progress:
                try:
                    print(f"\n=== Debug: Looking up details for schedule item ID {self.job_in_progress} ===")
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
                    # Continue to fallback mechanism

            # Fallback approach: Try to match active_program with program_name in master order
            if self.active_program:
                try:
                    print(f"\n=== Debug: Fallback - Looking up program with name '{self.active_program}' ===")

                    # Find programs matching the active_program
                    matching_programs = []
                    try:
                        print("START===================================================")
                        print("The active program from the machine_raw_live is:", self.active_program)

                        # Use direct SQL queries instead of generator expression to avoid tuple index error
                        # Method 1: Exact match on program_name
                        programs_list = Program.select()[:]  # Get all programs first
                        for program in programs_list:
                            if program.program_name == self.active_program:
                                matching_programs.append(program)

                        print(f"Found {len(matching_programs)} programs matching name exactly")

                        # Method 2: Match program name at the end of the path
                        if not matching_programs and self.active_program:
                            print("Trying to match program name at the end of the path...")
                            try:
                                program_file = self.active_program.split('\\')[-1]
                                print(f"Extracted filename: {program_file}")

                                for program in programs_list:
                                    if program.program_name and program.program_name.endswith(program_file):
                                        matching_programs.append(program)

                                print(f"Found {len(matching_programs)} programs matching filename at end of path")
                            except Exception as split_error:
                                print(f"Error splitting path: {str(split_error)}")

                        # Method 3: Try matching program_number
                        if not matching_programs and self.active_program:
                            print("Trying to match program number...")

                            for program in programs_list:
                                if program.program_number == self.active_program:
                                    matching_programs.append(program)

                            print(f"Found {len(matching_programs)} programs matching program number")

                        # Method 4: Try substring matching (as a last resort)
                        if not matching_programs and self.active_program:
                            print("Trying substring matching as a final attempt...")

                            # Extract filename without path
                            try:
                                # Try with backslash path separator
                                if '\\' in self.active_program:
                                    filename = self.active_program.split('\\')[-1]
                                # Try with forward slash path separator
                                elif '/' in self.active_program:
                                    filename = self.active_program.split('/')[-1]
                                else:
                                    filename = self.active_program

                                # Remove extension if present
                                if '.' in filename:
                                    filename_no_ext = filename.split('.')[0]
                                else:
                                    filename_no_ext = filename

                                print(f"Looking for programs containing: '{filename_no_ext}'")

                                for program in programs_list:
                                    if program.program_name and filename_no_ext in program.program_name:
                                        print(f"Found substring match: '{filename_no_ext}' in '{program.program_name}'")
                                        matching_programs.append(program)

                                print(f"Found {len(matching_programs)} programs with substring matching")
                            except Exception as substring_error:
                                print(f"Error in substring matching: {str(substring_error)}")

                        print("===================================================END")
                    except Exception as match_error:
                        print(f"Error during program matching: {str(match_error)}")
                        import traceback
                        print(traceback.format_exc())

                    # Process each matching program
                    for program in matching_programs:
                        try:
                            print(f"Processing program: ID={program.id}, Name={program.program_name}")

                            # Check for operation relationship
                            operation = None
                            try:
                                if hasattr(program, 'operation'):
                                    operation = program.operation
                                    if operation:
                                        print(
                                            f"Found operation: ID={operation.id}, Number={operation.operation_number}")
                                    else:
                                        print("Program has no associated operation (operation is None)")
                                        continue
                                else:
                                    print("Program has no 'operation' attribute")

                                    # Try to get operation through the database relationship
                                    print(f"Trying to find operation for program ID {program.id} through database...")
                                    program_with_relations = Program.get(id=program.id)
                                    if program_with_relations:
                                        operation = program_with_relations.operation
                                        if operation:
                                            print(f"Found operation through database lookup: ID={operation.id}")
                                        else:
                                            print("No operation found through database lookup")
                                            continue
                                    else:
                                        print(f"Could not find program with ID {program.id}")
                                        continue
                            except AttributeError as attr_error:
                                print(f"Attribute error checking operation: {attr_error}")
                                continue

                            # Check for order relationship
                            order = None
                            try:
                                if operation and hasattr(operation, 'order'):
                                    order = operation.order
                                    if order:
                                        print(f"Found order: PO={order.production_order}, Part={order.part_number}")
                                    else:
                                        print("Operation has no associated order (order is None)")
                                        continue
                                elif operation:
                                    print("Operation has no 'order' attribute")

                                    # Try to get order through the database relationship
                                    print(f"Trying to find order for operation ID {operation.id} through database...")
                                    operation_with_relations = Operation.get(id=operation.id)
                                    if operation_with_relations:
                                        order = operation_with_relations.order
                                        if order:
                                            print(f"Found order through database lookup: PO={order.production_order}")
                                        else:
                                            print("No order found through database lookup")
                                            continue
                                    else:
                                        print(f"Could not find operation with ID {operation.id}")
                                        continue
                                else:
                                    print("Operation is None, cannot check for order")
                                    continue
                            except AttributeError as attr_error:
                                print(f"Attribute error checking order: {attr_error}")
                                continue

                            # Return order details if all relationships are valid
                            if order and operation:
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
                                print("Missing required order or operation relationship")
                        except Exception as program_process_error:
                            print(f"Error processing program {program.id}: {str(program_process_error)}")
                            import traceback
                            print(traceback.format_exc())
                            continue  # Try next program

                    print(f"No valid program/operation/order relationship found for '{self.active_program}'")

                except Exception as program_error:
                    print(f"Error in program lookup fallback: {str(program_error)}")
                    import traceback
                    print(traceback.format_exc())

            # No match found, return None
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
