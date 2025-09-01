from datetime import datetime, timedelta, date
import pandas as pd
from typing import Dict, Tuple, List
from decimal import Decimal
from pony.orm import select, db_session
from app.models import Operation, Order, Machine, Status, RawMaterial, Project, InventoryStatus, MachineStatus, \
    PartScheduleStatus


def is_working_day(dt: datetime) -> bool:
    """
    Check if the given datetime falls on a working day (Monday to Saturday).
    Returns False for Sunday (weekday 6).
    """
    return dt.weekday() != 6  # 6 is Sunday


def get_next_working_day(dt: datetime) -> datetime:
    """
    Get the next working day from the given datetime.
    If it's already a working day, return as is.
    If it's Sunday, move to Monday.
    """
    while not is_working_day(dt):
        dt = dt + timedelta(days=1)
    return dt


def adjust_to_shift_hours(time: datetime) -> datetime:
    """
    Adjust time to fit within shift hours (6 AM to 10 PM) in IST
    Ensures the time is treated as IST and falls on a working day
    """
    # First, ensure it's a working day
    time = get_next_working_day(time)

    # Then adjust for shift hours
    if time.hour < 6:
        return time.replace(hour=6, minute=0, second=0, microsecond=0)
    elif time.hour >= 22:
        # Move to next working day at 6 AM
        next_day = time + timedelta(days=1)
        next_day = get_next_working_day(next_day)
        return next_day.replace(hour=6, minute=0, second=0, microsecond=0)
    return time


def get_next_shift_start(dt: datetime) -> datetime:
    """
    Get the next shift start time, ensuring it's on a working day.
    """
    # If it's past 22:00 or before 6:00, move to next day at 6:00
    if dt.hour >= 22:
        next_day = dt + timedelta(days=1)
    elif dt.hour < 6:
        next_day = dt
    else:
        # Currently within working hours, return next day
        next_day = dt + timedelta(days=1)

    # Ensure it's a working day
    next_day = get_next_working_day(next_day)
    return next_day.replace(hour=6, minute=0, second=0, microsecond=0)


def get_shift_end(dt: datetime) -> datetime:
    """
    Get the shift end time for the given datetime (22:00 on the same day).
    """
    return dt.replace(hour=22, minute=0, second=0, microsecond=0)


def validate_shift_timing(start_time: datetime, end_time: datetime) -> Tuple[datetime, datetime]:
    """
    Validate and adjust shift timing to ensure operations fit within working hours.
    Returns adjusted start and end times.
    Includes operations that end exactly at 22:00 (10:00 PM).
    """
    # Ensure start time is within shift hours
    adjusted_start = adjust_to_shift_hours(start_time)
    
    # If end time extends beyond shift hours, adjust to shift end
    # Include operations that end exactly at 22:00 (10:00 PM)
    shift_end = get_shift_end(adjusted_start)
    if end_time > shift_end:  # Changed from >= to > to include exactly at 22:00
        adjusted_end = shift_end
    else:
        adjusted_end = end_time
    
    return adjusted_start, adjusted_end


@db_session
def schedule_operations(df: pd.DataFrame, component_quantities: Dict[Tuple[str, str], int],
                        lead_times: Dict[str, datetime] = None) -> \
        Tuple[pd.DataFrame, datetime, float, Dict, Dict, List[str]]:
    """Main scheduling function that ONLY uses activation time for scheduling.
    
    IMPORTANT: No operations are invalidated - all operations are scheduled regardless of:
    - Shift hour constraints (6 AM to 10 PM)
    - Working day constraints (Monday to Saturday)
    - Timing violations
    
    Operations may extend beyond shift hours or working days but will still be scheduled.
    """

    # print("\n==== ACTIVATION TIME ONLY SCHEDULING WITH SHIFT HOURS AND WORKING DAYS ====")
    # print(f"Total Parts Requested: {len(component_quantities)}")
    # print(f"Component Quantities: {component_quantities}")

    if df.empty:
        print("ERROR: Input DataFrame is empty!")
        return pd.DataFrame(), datetime.now(), 0.0, {}, {}, ["Empty input DataFrame"]

    # FILTER OUT DEFAULT MACHINES BEFORE ANY SCHEDULING LOGIC
    # print("\n==== FILTERING DEFAULT MACHINES ====")
    default_machine_ids = [
        m.id for m in Machine.select()
        if m.type == "Default" and m.make == "Default" and m.model == "Default"
    ]
    # print(f"Default Machine IDs to exclude: {default_machine_ids}")

    if default_machine_ids:
        original_shape = df.shape
        df = df[~df['machine_id'].isin(default_machine_ids)]
        # print(f"Filtered DataFrame: {original_shape} -> {df.shape}")

        if df.empty:
            # print("WARNING: No operations remain after filtering out default machines!")
            return pd.DataFrame(), datetime.now(), 0.0, {}, {}, [
                "No operations remain after filtering default machines"]
    else:
        print("No default machines found to filter")

    # Get activation times - THIS IS THE ONLY CONSTRAINT WE CARE ABOUT
    part_activation_times = {}
    active_parts = {}

    for part_status in PartScheduleStatus.select():
        key = (part_status.part_number, part_status.production_order)
        if part_status.status == 'active':
            # Convert to IST
            ist_offset = timedelta(hours=5, minutes=30)
            activation_time_ist = part_status.updated_at + ist_offset
            # Adjust activation time to shift hours and ensure working day
            activation_time_ist = adjust_to_shift_hours(activation_time_ist)
            part_activation_times[key] = activation_time_ist

            # If this part is in our request, add it to active parts
            if key in component_quantities:
                active_parts[key] = component_quantities[key]

    # print("\n==== ACTIVATION TIMES FOUND (ADJUSTED TO SHIFT HOURS AND WORKING DAYS) ====")
    # for key, activation_time in part_activation_times.items():
    #     if key in active_parts:
    #         print(
    #             f"Part: {key[0]}, PO: {key[1]}, Activation: {activation_time.strftime('%Y-%m-%d %H:%M:%S')} ({activation_time.strftime('%A')})")

    if not active_parts:
        print("CRITICAL: No active parts found!")
        return pd.DataFrame(), datetime.now(), 0.0, {}, {}, ["No active parts found"]

    # Get order information for setup and cycle times
    order_info = {}
    for order in Order.select():
        key = (order.part_number, order.production_order)
        if key in active_parts:
            order_info[key] = {
                'order_id': order.id,
                'priority': order.project.priority if order.project else float('inf')
            }

    # Sort by priority (same logic as scheduling.py)
    sorted_parts = sorted(active_parts.keys(),
                          key=lambda x: part_activation_times.get(x, datetime.now()))

    # Reorder the dataframe based on sorted parts (same logic as scheduling.py)
    # Create a mapping of parts to sort order indices
    part_to_idx = {part: idx for idx, part in enumerate(sorted_parts)}

    # Add a sort_order column to the dataframe
    # Create a copy to avoid pandas SettingWithCopyWarning
    df = df.copy()
    df['sort_order'] = df.apply(
        lambda row: part_to_idx.get((row['partno'], row['production_order']), float('inf')),
        axis=1
    )

    # Sort the dataframe and filter for active parts (same as scheduling.py)
    df_sorted = df[
        df.apply(lambda row: (row['partno'], row['production_order']) in active_parts, axis=1)
    ].sort_values(by=['sort_order', 'sequence']).drop('sort_order', axis=1)

    # Update part_operations to use the sorted dataframe with proper sequence order
    part_operations = {}
    for (partno, production_order), group in df_sorted.groupby(['partno', 'production_order']):
        key = (partno, production_order)
        if key in active_parts:
            part_operations[key] = group.to_dict('records')

    # Enhanced Debug Logging (same as scheduling.py)
    # print("\n==== OPERATION SEQUENCE DIAGNOSTIC ====")
    # for (partno, production_order) in sorted_parts:
    #     if (partno, production_order) in part_operations:
    #         operations = part_operations[(partno, production_order)]
    #         print(f"\nPart {partno} (PO: {production_order}) Operations:")
    #         print(f"Operations Count: {len(operations)}")
    #         if operations:
    #             # Show operations in sequence order
    #             for op in operations:
    #                 print(f"  Sequence {op['sequence']}: {op['operation']} on Machine {op['machine_id']}")
    #     else:
    #         print(f"\nPart {partno} (PO: {production_order}): No operations found")

    schedule = []
    part_status = {}
    daily_production = {}

    # print("\n==== SCHEDULING PARTS WITH SHIFT AND WORKING DAY CONSTRAINTS ====")

    for partno, production_order in sorted_parts:
        key = (partno, production_order)
        quantity = active_parts[key]
        operations = part_operations.get(key, [])

        # USE ACTIVATION TIME DIRECTLY - NO OTHER CONSTRAINTS EXCEPT SHIFT HOURS AND WORKING DAYS
        activation_time = part_activation_times.get(key)
        if not activation_time:
            print(f"No activation time found for {partno} - {production_order}")
            continue

        # print(
        #     f"Scheduling {partno} (PO: {production_order}) starting at {activation_time} ({activation_time.strftime('%A')})")

        # Initialize current_time with activation time - this will flow sequentially through operations
        # CRITICAL: current_time will be updated after each operation completes
        current_time = activation_time
        unit_completion_times = {}

        # print(
        #     f"Initial current_time for {partno}: {current_time.strftime('%Y-%m-%d %H:%M:%S')} ({current_time.strftime('%A')})")

        # Validate that activation time respects shift hours
        validated_start, _ = validate_shift_timing(activation_time, activation_time)
        if validated_start != activation_time:
            # print(f"    Shift timing adjustment: {activation_time} -> {validated_start}")
            current_time = validated_start

        # Process each operation sequentially from activation time
        # CRITICAL: Operations are processed in strict sequence order (op['sequence'])
        # This matches the exact logic from scheduling.py
        for op_idx, op in enumerate(operations):
            machine_id = op['machine_id']

            # print(
            #     f"  Operation {op_idx + 1} (Sequence {op['sequence']} - {op['operation']}): Starting at {current_time.strftime('%Y-%m-%d %H:%M:%S')} ({current_time.strftime('%A')})")

            # Get operation details for timing
            order_id = order_info.get(key, {}).get('order_id')
            if order_id:
                operation = Operation.select(lambda o:
                                             o.order.id == order_id and
                                             o.operation_number == op['sequence']).first()

                if operation:
                    setup_minutes = float(operation.setup_time) * 60
                    cycle_minutes = float(operation.ideal_cycle_time) * 60
                else:
                    # Fallback if no operation found
                    setup_minutes = 30.0  # Default 30 min setup
                    cycle_minutes = 5.0  # Default 5 min cycle
            else:
                setup_minutes = 30.0
                cycle_minutes = 5.0

            # Use current_time as the start for this operation (sequential flow)
            operation_start_time = current_time

            # Apply working day and shift adjustment if needed
            operation_start_time = adjust_to_shift_hours(operation_start_time)
            if operation_start_time != current_time:
                print(
                    f"    Adjusted start time to: {operation_start_time.strftime('%Y-%m-%d %H:%M:%S')} ({operation_start_time.strftime('%A')})")

            # NOTE: No shift timing validation - all operations are scheduled regardless of timing
            # This ensures no operations are invalidated due to shift hour constraints

            # Schedule setup with shift and working day constraints
            setup_start = operation_start_time
            setup_end = setup_start + timedelta(minutes=setup_minutes)
            shift_end = get_shift_end(setup_start)

            # Check if setup crosses the shift end
            if setup_end > shift_end:
                # Schedule partial setup for current shift
                if setup_start < shift_end:
                    partial_setup_time = (shift_end - setup_start).total_seconds() / 60
                    schedule.append([
                        partno, op['operation'], machine_id,
                        setup_start, shift_end,
                        f"Setup({partial_setup_time:.0f}/{setup_minutes:.0f}min)",
                        production_order
                    ])

                # Calculate remaining setup for next working day
                remaining_setup = setup_minutes - (shift_end - setup_start).total_seconds() / 60
                next_start = get_next_shift_start(shift_end)

                # Continue setup across multiple working days if needed
                while remaining_setup > 0:
                    current_shift_end = get_shift_end(next_start)
                    daily_work_hours = (current_shift_end - next_start).total_seconds() / 60
                    setup_today = min(remaining_setup, daily_work_hours)
                    current_end = next_start + timedelta(minutes=setup_today)

                    schedule.append([
                        partno, op['operation'], machine_id,
                        next_start, current_end,
                        f"Setup({setup_minutes - remaining_setup + setup_today:.0f}/{setup_minutes:.0f}min)",
                        production_order
                    ])

                    remaining_setup -= setup_today
                    if remaining_setup > 0:
                        next_start = get_next_shift_start(current_shift_end)
                    else:
                        current_time = current_end
                        break
            else:
                # Setup fits within current shift (including ending exactly at shift end)
                schedule.append([
                    partno, op['operation'], machine_id,
                    setup_start, setup_end,
                    f"Setup({setup_minutes:.0f}min)",
                    production_order
                ])
                current_time = setup_end

            # Schedule production with shift and working day constraints
            # Current_time now represents the end of setup (whether single or multi-day)
            production_start = current_time
            total_production_time = cycle_minutes * quantity
            production_end = production_start + timedelta(minutes=total_production_time)
            shift_end = get_shift_end(production_start)

            # Check if production crosses shift boundaries
            # Include operations that end exactly at 22:00 (10:00 PM)
            if production_end > shift_end:
                # Calculate production in current shift
                work_minutes_today = (shift_end - production_start).total_seconds() / 60
                completion_ratio = work_minutes_today / total_production_time if total_production_time > 0 else 0
                pieces_today = int(quantity * completion_ratio)

                if work_minutes_today > 0 and pieces_today > 0:
                    schedule.append([
                        partno, op['operation'], machine_id,
                        production_start, shift_end,
                        f"Process({pieces_today}/{quantity}pcs)",
                        production_order
                    ])

                # Calculate remaining work
                remaining_time = total_production_time - work_minutes_today
                remaining_pieces = quantity - pieces_today
                next_start = get_next_shift_start(shift_end)
                pieces_completed = pieces_today

                # Continue production across multiple working days
                while remaining_time > 0 and remaining_pieces > 0:
                    current_shift_end = get_shift_end(next_start)
                    daily_work_hours = (current_shift_end - next_start).total_seconds() / 60
                    work_today = min(remaining_time, daily_work_hours)

                    if work_today > 0:
                        # Calculate pieces for this shift
                        pieces_ratio = work_today / remaining_time if remaining_time > 0 else 1
                        pieces_this_shift = min(remaining_pieces,
                                                max(1, int(remaining_pieces * pieces_ratio)))

                        current_end = next_start + timedelta(minutes=work_today)
                        pieces_completed += pieces_this_shift

                        schedule.append([
                            partno, op['operation'], machine_id,
                            next_start, current_end,
                            f"Process({pieces_completed}/{quantity}pcs)",
                            production_order
                        ])

                        remaining_time -= work_today
                        remaining_pieces -= pieces_this_shift

                    if remaining_time > 0 and remaining_pieces > 0:
                        next_start = get_next_shift_start(current_shift_end)
                    else:
                        # Production is complete - update current_time to the end of production
                        current_time = current_end
                        break
            else:
                # Production fits within current shift (including ending exactly at shift end)
                schedule.append([
                    partno, op['operation'], machine_id,
                    production_start, production_end,
                    f"Process({quantity}/{quantity}pcs)",
                    production_order
                ])
                # Update current_time to end of production
                current_time = production_end

            # print(
            #     f"    Operation {op_idx + 1} completed at: {current_time.strftime('%Y-%m-%d %H:%M:%S')} ({current_time.strftime('%A')})")

            # Set completion times for all units at the end of last operation
            if op_idx == len(operations) - 1:
                for unit_number in range(1, quantity + 1):
                    unit_completion_times[unit_number] = current_time

        # Track part status
        latest_completion = max(unit_completion_times.values()) if unit_completion_times else current_time
        status_key = f"{partno}_{production_order}"
        part_status[status_key] = {
            'partno': partno,
            'production_order': production_order,
            'scheduled_end_time': latest_completion,
            'priority': order_info.get(key, {}).get('priority', float('inf')),
            'completed_quantity': len(unit_completion_times),
            'total_quantity': quantity,
            'start_time': activation_time
        }

        # Track daily production (only for working days)
        if partno not in daily_production:
            daily_production[partno] = {}

        for unit_num, completion_time in unit_completion_times.items():
            completion_day = completion_time.date()
            # Only track production on working days
            if is_working_day(completion_time):
                if completion_day not in daily_production[partno]:
                    daily_production[partno][completion_day] = 0
                daily_production[partno][completion_day] += 1

    # Create final schedule DataFrame
    if not schedule:
        return pd.DataFrame(), datetime.now(), 0.0, daily_production, part_status, []

    schedule_df = pd.DataFrame(
        schedule,
        columns=["partno", "operation", "machine_id", "start_time", "end_time", "quantity", "production_order"]
    )

    if schedule_df.empty:
        return schedule_df, datetime.now(), 0.0, daily_production, part_status, []

    # NOTE: No validation is performed - all operations are scheduled regardless of timing
    # This ensures no operations are invalidated due to shift hours or working day constraints
    # print("âœ“ All operations are scheduled regardless of timing constraints")

    overall_end_time = max(schedule_df['end_time'])

    # Calculate overall time from earliest activation
    earliest_activation = min(part_activation_times.values()) if part_activation_times else datetime.now()
    overall_time = (overall_end_time - earliest_activation).total_seconds() / 60

    # Calculate working days between start and end
    working_days_used = 0
    current_date = earliest_activation.date()
    end_date = overall_end_time.date()

    while current_date <= end_date:
        if is_working_day(datetime.combine(current_date, datetime.min.time())):
            working_days_used += 1
        current_date += timedelta(days=1)

    # print(f"Scheduling complete. Total time: {overall_time} minutes")
    # print(
    #     f"Schedule spans from {earliest_activation.strftime('%Y-%m-%d %H:%M:%S')} ({earliest_activation.strftime('%A')}) to {overall_end_time.strftime('%Y-%m-%d %H:%M:%S')} ({overall_end_time.strftime('%A')})")
    # print(f"Working days used: {working_days_used}")
    # print(f"Shift timing constraints: 6:00 AM to 10:00 PM (16 hours per working day)")
    # print(f"Note: Operations ending exactly at 10:00 PM (22:00) are considered valid")
    # print(f"IMPORTANT: No operations are invalidated - all operations are scheduled regardless of timing")
    # print(f"Note: Shift timing functions are used for guidance but do not prevent scheduling")
    # print(f"Default machines excluded: {len(default_machine_ids)}")
    # print(f"Operations scheduled: {len(schedule_df)}")

    return schedule_df, overall_end_time, overall_time, daily_production, part_status, []
