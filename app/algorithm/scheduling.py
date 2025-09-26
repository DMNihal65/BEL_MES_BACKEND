

from datetime import datetime, timedelta, date
import pandas as pd
from typing import Dict, Tuple, List
from decimal import Decimal
from pony.orm import select, db_session
from app.models import Operation, Order, Machine, Status, RawMaterial, Project, InventoryStatus, MachineStatus, \
    PartScheduleStatus, WorkCenter
from app.crud.pdc import (
    create_pdc_record,
    update_pdc_record,
    get_pdc_by_part_number_and_po,
    upsert_pdc_record,
)


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
    Adjust time to fit within shift hours (6 AM to 5 PM) in IST
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


def schedule_operations(df: pd.DataFrame, component_quantities: Dict[Tuple[str, str], int],
                        lead_times: Dict[str, datetime] = None) -> \
        Tuple[pd.DataFrame, datetime, float, Dict, Dict, List[str]]:
    """Main scheduling function that creates a production schedule based on operations data"""

    # COMPREHENSIVE DIAGNOSTIC LOGGING
    # print("\n==== SCHEDULING FUNCTION: COMPREHENSIVE DIAGNOSTIC ====")
    # print(f"Total Parts Requested: {len(component_quantities)}")
    # print(f"Component Quantities: {component_quantities}")
    # print(f"Input DataFrame Shape: {df.shape}")
    # print(f"Unique Parts in DataFrame: {df['partno'].unique()}")

    # Enhanced Debug Logging
    for (partno, production_order) in component_quantities.keys():
        part_df = df[(df['partno'] == partno) & (df['production_order'] == production_order)]
        # print(f"\nPart {partno} Debug:")
        # print(f"Operations Count: {len(part_df)}")
        # if not part_df.empty:
            # print(part_df[['operation', 'machine_id', 'sequence', 'time']].to_string())

    if df.empty:
        # print("ERROR: Input DataFrame is empty!")
        return pd.DataFrame(), datetime.now(), 0.0, {}, {}, ["Empty input DataFrame"]

    # EARLY FILTERING: Remove default machines before any scheduling logic
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

    # Filter operations based on WorkCenter's is_schedulable flag
    schedulable_work_centers = [wc.id for wc in WorkCenter.select() if wc.is_schedulable]

    # print(f"\n==== SCHEDULABLE WORK CENTERS ====")
    # print(f"Schedulable Work Center IDs: {schedulable_work_centers}")

    # Create a lookup of machine_ids to their work_center_ids
    machine_to_work_center = {}
    for machine in Machine.select():
        machine_to_work_center[machine.id] = machine.work_center.id

    # Filter out operations for machines in non-schedulable work centers
    filtered_df = df[
        df['machine_id'].apply(lambda m_id: machine_to_work_center.get(m_id) in schedulable_work_centers)]

    # print(f"After WorkCenter filtering: {df.shape} -> {filtered_df.shape}")

    # If filtering removed all operations, return empty
    if filtered_df.empty:
        # print("WARNING: No operations remain after filtering for schedulable work centers!")
        return pd.DataFrame(), datetime.now(), 0.0, {}, {}, ["No operations in schedulable work centers"]

    # Replace original df with filtered version
    df = filtered_df

    # Gather all order and part status information upfront
    part_status_map = {}
    part_activation_times = {}

    for part_status in PartScheduleStatus.select():
        key = (part_status.part_number, part_status.production_order)
        part_status_map[key] = part_status.status
        if part_status.status == 'active':
            ist_offset = timedelta(hours=5, minutes=30)
            activation_time_ist = part_status.updated_at + ist_offset
            part_activation_times[key] = activation_time_ist

    # Enhanced Filtering for Active Parts
    active_parts = {
        (partno, po): qty
        for (partno, po), qty in component_quantities.items()
        if part_status_map.get((partno, po), 'inactive') == 'active'
    }

    # print("\n==== ACTIVE PARTS DIAGNOSTIC ====")
    for (partno, production_order), qty in active_parts.items():
        activation_time = part_activation_times.get((partno, production_order))
        activation_time_str = activation_time.strftime("%Y-%m-%d %H:%M:%S") if activation_time else "None"

        # Additional Part Validation
        part_df = df[df['partno'] == partno]
        # print(f"Part: {partno}")
        # print(f"  Quantity: {qty}")
        # print(f"  Activation Time (IST): {activation_time_str}")
        # print(f"  Operations Count: {len(part_df)}")

        if part_df.empty:
            print(f"  WARNING: No operations found for part {partno}")

    # Track skipped parts with more detailed reasoning
    skipped_parts = [
        f"Skipped {partno}: Status {part_status_map.get((partno, production_order), 'not found')}"
        for (partno, production_order) in component_quantities.keys()
        if part_status_map.get((partno, production_order), 'inactive') != 'active'
    ]

    # print("\n==== SKIPPED PARTS ====")
    # for skipped in skipped_parts:
    #     print(skipped)

    if not active_parts:
        print("CRITICAL: No parts are marked as active for scheduling")
        return pd.DataFrame(), datetime.now(), 0.0, {}, {}, ["No parts are marked as active for scheduling"]

    # Create a more detailed tracking map that includes production orders
    production_orders = {}

    with db_session:
        # Collect all production orders for each part number
        for order in Order.select():
            if order.part_number not in production_orders:
                production_orders[order.part_number] = []

            production_orders[order.part_number].append({
                'production_order': order.production_order,
                'order_id': order.id,
                'priority': order.project.priority if order.project else float('inf'),
                'lead_time': order.project.delivery_date if order.project else None,
                'raw_material': order.raw_material
            })

    # Get project priorities and lead times
    part_priorities = {}
    part_lead_times = {}
    order_info = {}

    # Gather all order, priority, and lead time information
    for (partno, production_order) in active_parts.keys():
        orders = list(Order.select(lambda o: o.part_number == partno))
        if orders:
            # For backwards compatibility, use the highest priority order for sorting
            highest_priority = min([
                order.project.priority if order.project else float('inf')
                for order in orders
            ], default=float('inf'))

            part_priorities[(partno, production_order)] = highest_priority

            # Store earliest lead time for reference
            lead_times = [order.project.delivery_date for order in orders
                          if order.project and order.project.delivery_date]
            if lead_times:
                part_lead_times[(partno, production_order)] = min(lead_times)

    # Sort parts by priority only (lower number = higher priority)
    sorted_parts = sorted(
        active_parts.keys(),
        key=lambda x: (part_priorities.get(x, float('inf')))
    )

    # Reorder the dataframe based on sorted parts
    # Create a mapping of parts to sort order indices
    part_to_idx = {part: idx for idx, part in enumerate(sorted_parts)}

    # Add a sort_order column to the dataframe
    df['sort_order'] = df.apply(
        lambda row: part_to_idx.get((row['partno'], row['production_order']), float('inf')),
        axis=1
    )

    # Sort the dataframe and filter for active parts
    df_sorted = df[
        df.apply(lambda row: (row['partno'], row['production_order']) in active_parts, axis=1)
    ].sort_values(by=['sort_order', 'sequence']).drop('sort_order', axis=1)

    # Fetch raw materials with their InventoryStatus
    raw_materials_query = select((o.part_number, o.raw_material, ist, o.raw_material.available_from)
                                 for o in Order
                                 for ist in InventoryStatus
                                 if o.raw_material and o.raw_material.status == ist)
    raw_materials = {
        part_number: (
            ist.name == 'Available',
            rm.quantity,
            rm.unit,
            rm.available_from
        ) for part_number, rm, ist, available_from in raw_materials_query
    }

    # Fetch machine statuses with their status details (excluding default machines)
    machine_statuses_query = select((m, ms, s, ms.available_from, ms.available_to)
                                    for m in Machine
                                    for ms in m.status
                                    for s in Status
                                    if ms.status == s and m.id not in default_machine_ids)
    machine_statuses = {
        m.id: {
            'machine_make': m.make,
            'status_id': s.id,
            'status_name': s.name,
            'status_description': s.description,
            'machine_status_description': ms.description,
            'available_from': ms.available_from,  # status start
            'available_to': ms.available_to  # status end (may be None)
        } for m, ms, s, _, _ in machine_statuses_query
    }

    part_operations = {
        (partno, production_order): group.to_dict('records')
        for (partno, production_order), group in df_sorted.groupby(['partno', 'production_order'])
    }

    # Get current time in IST as default start date, ensure it's a working day
    ist_offset = timedelta(hours=5, minutes=30)
    default_start_date = datetime.now() + ist_offset
    default_start_date = adjust_to_shift_hours(default_start_date)

    # Find the earliest activation time across all active parts to use as the global start
    earliest_activation_time = None
    for key in active_parts.keys():
        if key in part_activation_times:
            if earliest_activation_time is None or part_activation_times[key] < earliest_activation_time:
                earliest_activation_time = part_activation_times[key]

    # Use the earliest activation time or default to current time in IST, ensure working day
    if earliest_activation_time:
        global_start_date = adjust_to_shift_hours(earliest_activation_time)
    else:
        global_start_date = default_start_date

    # Log the global start date used for scheduling
    # print(
    #     f"Global start date (IST, Working Day): {global_start_date.strftime('%Y-%m-%d %H:%M:%S')} ({global_start_date.strftime('%A')})")

    # Initialize machine end times with the earliest start date (excluding default machines)
    non_default_machines = [m for m in df_sorted["machine_id"].unique() if m not in default_machine_ids]
    machine_end_times = {machine: global_start_date for machine in non_default_machines}

    schedule = []
    daily_production = {}
    part_status = {}
    partially_completed = []

    def check_machine_status(machine_id: int, time: datetime) -> Tuple[bool, datetime]:
        """
        Check if a machine is available at a given time.
        Now also considers working days and excludes default machines.
        """
        # Skip check for default machines (they shouldn't be scheduled anyway)
        if machine_id in default_machine_ids:
            return False, None

        # First check if it's a working day
        if not is_working_day(time):
            next_working = get_next_working_day(time.replace(hour=6, minute=0, second=0, microsecond=0))
            return False, next_working

        ms = machine_statuses.get(machine_id)
        if not ms:
            # No status record â†’ assume machine is available (default)
            return True, time

        status = ms['status_name'].upper()
        af = ms['available_from']  # Start of status period
        at = ms['available_to']  # End of status period (may be None)

        if status == 'OFF':
            # OFF window: unavailable between af and at
            if af and at:
                if af <= time < at:
                    # Time falls within OFF window
                    # Ensure the return time is on a working day
                    next_available = get_next_working_day(at) if not is_working_day(at) else at
                    return False, next_available
                else:
                    # Time is outside OFF window, machine is available
                    return True, time
            elif af and not at:
                # Machine is OFF starting from 'af' indefinitely
                if time >= af:
                    return False, None  # Not available and won't be
                else:
                    return True, time  # Available now until 'af'
            else:
                # Malformed status record
                return True, time

        # Status is ON or any other status
        if status == 'ON':
            if af:
                if time < af:
                    # Machine will be ON from 'af', ensure it's on a working day
                    next_available = get_next_working_day(af) if not is_working_day(af) else af
                    return False, next_available
                else:
                    # Machine is ON now
                    return True, time
            else:
                # Machine is ON with no start time specified
                return True, time

        # Any other status - default to available
        return True, time

    def find_last_available_operation(operations: List[dict], current_time: datetime) -> int:
        """Find the last operation that can be performed in sequence (excluding default machines)"""
        last_available = -1
        current_op_time = current_time

        for idx, op in enumerate(operations):
            machine_id = op['machine_id']

            # Skip default machines
            if machine_id in default_machine_ids:
                continue

            machine_available, available_time = check_machine_status(machine_id, current_op_time)

            if not machine_available and available_time is None:
                break

            if available_time:
                current_op_time = available_time

            last_available = idx
            op_time = float(op['time']) * 60
            current_op_time += timedelta(minutes=op_time)

        return last_available

    def schedule_batch_operations(partno: str, operations: List[dict], quantity: int, start_time: datetime,
                                  order_id: int = None, production_order: str = None, order_raw_material=None) -> Tuple[
        List[list], int, Dict[int, datetime]]:
        """Schedule operations for a batch of components with working day validation (excluding default machines)"""

        # Ensure start_time is on a working day
        start_time = adjust_to_shift_hours(start_time)

        # Filter out operations on default machines
        operations = [op for op in operations if op['machine_id'] not in default_machine_ids]

        if not operations:
            print(f"No non-default machine operations found for part {partno}")
            return [], 0, {}

        # Use the provided order details or find the order
        if order_id is None:
            # Find the specific order if not provided
            try:
                orders = list(Order.select(lambda o: o.part_number == partno))
                if not orders:
                    print(f"No order found for part {partno}")
                    return [], 0, {}

                # Use the first order with a raw material, or the first order if none have raw materials
                order = next((o for o in orders if o.raw_material), orders[0])
                raw_material = order.raw_material
                production_order = order.production_order
            except Exception as e:
                print(f"Error retrieving order for {partno}: {str(e)}")
                return [], 0, {}
        else:
            # Use the provided order details
            try:
                order = Order[order_id]
                raw_material = order_raw_material or order.raw_material
            except Exception as e:
                print(f"Error retrieving order with ID {order_id}: {str(e)}")
                return [], 0, {}

        if not raw_material:
            print(f"No raw material found for part {partno}, production order {production_order}")
            return [], 0, {}

        # Detailed raw material status check
        raw_material_status = raw_material.status

        # Determine the effective start time
        effective_start_time = start_time

        # Enhanced raw material availability check
        # print(f"\nRaw Material Availability Check for {partno} (Production Order: {production_order}):")
        # print(f"Status: {raw_material_status.name}")
        # print(f"Available From: {raw_material.available_from}")
        # print(f"Initial Operation Start Time: {effective_start_time}")

        # Comprehensive availability check
        if raw_material_status.name != 'Available':
            # print(f"Raw material for {partno} (Production Order: {production_order}) is not in 'Available' status")
            return [], 0, {}

        # If raw material is available from a future time
        if raw_material.available_from:
            # Compare times, ensuring we respect the raw material's availability
            if effective_start_time < raw_material.available_from:
                # print(
                #     f"Adjusting operation time from {effective_start_time} to raw material available time {raw_material.available_from}")

                # Set effective start time to the raw material's available_from time
                effective_start_time = raw_material.available_from

                # Adjust to the next available shift start if needed, ensuring working day
                effective_start_time = adjust_to_shift_hours(effective_start_time)

                # print(f"Adjusted operation time after working day and shift hour consideration: {effective_start_time}")

        # Prepare for scheduling
        batch_schedule = []
        operation_time = effective_start_time
        unit_completion_times = {}
        cumulative_pieces = {}
        operation_setup_done = {}

        # Debug print to verify effective start time
        # print(
        #     f"Final Effective Start Time for {partno} (Production Order: {production_order}): {effective_start_time} ({effective_start_time.strftime('%A')})")

        # Rest of the scheduling logic with working day validation
        last_available_idx = find_last_available_operation(operations, operation_time)
        if last_available_idx < 0:
            print(f"No available operations found for {partno} (Production Order: {production_order})")
            return [], 0, {}

        available_operations = operations[:last_available_idx + 1]

        for op_idx, op in enumerate(available_operations):
            machine_id = op['machine_id']

            # Double check - skip default machines
            if machine_id in default_machine_ids:
                continue

            operation_key = f"{op['operation']}_{machine_id}"

            if operation_key not in cumulative_pieces:
                cumulative_pieces[operation_key] = 0
                operation_setup_done[operation_key] = False

            # Get setup time and cycle time
            operation = Operation.select(lambda o:
                                         o.order.id == order.id and
                                         o.operation_number == op['sequence']).first()

            if not operation:
                # Fallback to any operation with matching sequence
                operation = Operation.select(lambda o:
                                             o.order.part_number == partno and
                                             o.operation_number == op['sequence']).first()
                if not operation:
                    continue

            setup_minutes = float(operation.setup_time) * 60
            cycle_minutes = float(operation.ideal_cycle_time) * 60

            current_time = operation_time

            # Check machine availability for the current operation (includes working day check)
            machine_available, next_available_time = check_machine_status(machine_id, current_time)

            # If machine is not available now but will be available later
            if not machine_available:
                if next_available_time is None:
                    # print(
                    #     f"Machine {machine_id} is unavailable with no estimated return time for {partno}, operation {op['operation']}")
                    continue  # Skip this operation
                else:
                    # Update current time to when machine becomes available
                    # print(
                    #     f"Machine {machine_id} is unavailable until {next_available_time} for {partno}, operation {op['operation']}")
                    current_time = next_available_time

            # Adjust to shift hours and ensure working day
            current_time = adjust_to_shift_hours(current_time)

            # Use the later of current time or machine end time
            current_time = max(current_time, machine_end_times.get(machine_id, current_time))
            operation_start = current_time

            # Handle setup time with working day validation
            if not operation_setup_done[operation_key]:
                setup_end = operation_start + timedelta(minutes=setup_minutes)
                shift_end = get_shift_end(operation_start)

                # Check if setup crosses the shift end
                if setup_end > shift_end:
                    # Log the partial setup scheduled today
                    batch_schedule.append([
                        partno, op['operation'], machine_id,
                        operation_start, shift_end,
                        f"Setup({int((shift_end - operation_start).total_seconds() / 60)}/{setup_minutes}min)",
                        production_order
                    ])

                    # Calculate remaining setup for next working day(s)
                    next_start = get_next_shift_start(shift_end)
                    remaining_setup = setup_minutes - (shift_end - operation_start).total_seconds() / 60

                    # Before continuing with setup next day, check if machine will be available
                    while remaining_setup > 0:
                        # Check machine availability for the next day setup (includes working day check)
                        machine_available, next_available_time = check_machine_status(machine_id, next_start)

                        if not machine_available:
                            if next_available_time is None:
                                # print(f"Machine {machine_id} is permanently unavailable for remaining setup")
                                break  # Can't complete setup
                            else:
                                # Adjust next start time to when machine becomes available
                                next_start = adjust_to_shift_hours(next_available_time)
                                # print(f"Next setup will start at {next_start} when machine becomes available")

                        current_shift_end = get_shift_end(next_start)
                        setup_possible = min(remaining_setup, (current_shift_end - next_start).total_seconds() / 60)
                        current_end = next_start + timedelta(minutes=setup_possible)

                        batch_schedule.append([
                            partno, op['operation'], machine_id,
                            next_start, current_end,
                            f"Setup({setup_minutes - remaining_setup + setup_possible}/{setup_minutes}min)",
                            production_order
                        ])

                        remaining_setup -= setup_possible
                        if remaining_setup > 0:
                            next_start = get_next_shift_start(current_shift_end)

                        current_time = current_end
                    operation_start = current_end
                    operation_setup_done[operation_key] = True
                else:
                    batch_schedule.append([
                        partno, op['operation'], machine_id,
                        operation_start, setup_end,
                        f"Setup({setup_minutes}/{setup_minutes}min)",
                        production_order
                    ])
                    operation_start = setup_end
                    current_time = setup_end

                    operation_setup_done[operation_key] = True

            # Process production with working day validation
            total_processing_time = cycle_minutes * quantity
            processing_end = operation_start + timedelta(minutes=total_processing_time)
            shift_end = get_shift_end(operation_start)

            # Function to check for machine unavailability windows within a time period
            def find_machine_off_periods(machine_id, start_time, end_time):
                """Find periods when the machine is OFF within the given time range"""
                if machine_id in default_machine_ids:
                    return [(start_time, end_time)]  # Treat entire period as unavailable

                ms = machine_statuses.get(machine_id)
                off_periods = []

                if not ms or ms['status_name'].upper() != 'OFF':
                    return off_periods

                af = ms['available_from']  # Start of OFF period
                at = ms['available_to']  # End of OFF period

                if af and at and af < end_time and at > start_time:
                    # Calculate overlap of OFF period with our time range
                    overlap_start = max(start_time, af)
                    overlap_end = min(end_time, at)

                    if overlap_start < overlap_end:
                        off_periods.append((overlap_start, overlap_end))

                return off_periods

            # Check if processing crosses shifts or working days
            if processing_end > shift_end:
                # Calculate production in current shift
                work_minutes_today = (shift_end - operation_start).total_seconds() / 60
                completion_ratio = work_minutes_today / total_processing_time if total_processing_time > 0 else 0
                pieces_today = int(quantity * completion_ratio)

                # Check for machine OFF periods in current shift
                off_periods = find_machine_off_periods(machine_id, operation_start, shift_end)

                if off_periods:
                    # Handle each segment separately
                    current_segment_start = operation_start
                    remaining_minutes = work_minutes_today
                    pieces_processed = 0

                    for off_start, off_end in off_periods:
                        # Process until off_start
                        if current_segment_start < off_start:
                            segment_minutes = (off_start - current_segment_start).total_seconds() / 60
                            segment_ratio = segment_minutes / total_processing_time if total_processing_time > 0 else 0
                            segment_pieces = int(quantity * segment_ratio)

                            new_cumulative = min(cumulative_pieces[operation_key] + segment_pieces, quantity)

                            if segment_minutes > 0:
                                batch_schedule.append([
                                    partno, op['operation'], machine_id,
                                    current_segment_start, off_start,
                                    f"Process({new_cumulative}/{quantity}pcs)",
                                    production_order
                                ])
                                cumulative_pieces[operation_key] = new_cumulative
                                pieces_processed += segment_pieces

                        # Skip the off period
                        current_segment_start = off_end

                    # Process after the last off period until shift end
                    if current_segment_start < shift_end:
                        segment_minutes = (shift_end - current_segment_start).total_seconds() / 60
                        segment_ratio = segment_minutes / total_processing_time if total_processing_time > 0 else 0
                        segment_pieces = int(quantity * segment_ratio)

                        new_cumulative = min(cumulative_pieces[operation_key] + segment_pieces, quantity)

                        if segment_minutes > 0:
                            batch_schedule.append([
                                partno, op['operation'], machine_id,
                                current_segment_start, shift_end,
                                f"Process({new_cumulative}/{quantity}pcs)",
                                production_order
                            ])
                            cumulative_pieces[operation_key] = new_cumulative
                            pieces_processed += segment_pieces
                else:
                    # No OFF periods, process normally
                    new_cumulative = min(cumulative_pieces[operation_key] + pieces_today, quantity)
                    if work_minutes_today > 0:
                        batch_schedule.append([
                            partno, op['operation'], machine_id,
                            operation_start, shift_end,
                            f"Process({new_cumulative}/{quantity}pcs)",
                            production_order
                        ])
                        cumulative_pieces[operation_key] = new_cumulative

                # Calculate remaining work
                remaining_time = total_processing_time - work_minutes_today
                remaining_pieces = quantity - cumulative_pieces[operation_key]

                next_start = get_next_shift_start(shift_end)

                # Process remaining pieces across future working days
                while remaining_time > 0 and remaining_pieces > 0:
                    # Check machine availability for next day's work (includes working day check)
                    machine_available, next_available_time = check_machine_status(machine_id, next_start)

                    if not machine_available:
                        if next_available_time is None:
                            print(f"Machine {machine_id} is permanently unavailable for remaining pieces")
                            break  # Can't complete production
                        else:
                            # Adjust next start time to when machine becomes available
                            next_start = adjust_to_shift_hours(next_available_time)
                            print(f"Next processing will start at {next_start} when machine becomes available")

                    current_shift_end = get_shift_end(next_start)
                    work_possible = min(remaining_time, (current_shift_end - next_start).total_seconds() / 60)
                    current_end = next_start + timedelta(minutes=work_possible)

                    # Check for machine OFF periods in this shift
                    off_periods = find_machine_off_periods(machine_id, next_start, current_end)

                    if off_periods:
                        # Handle each segment separately
                        current_segment_start = next_start

                        for off_start, off_end in off_periods:
                            # Process until off_start
                            if current_segment_start < off_start:
                                segment_minutes = (off_start - current_segment_start).total_seconds() / 60
                                segment_ratio = segment_minutes / remaining_time
                                segment_pieces = min(remaining_pieces,
                                                     int(remaining_pieces * segment_ratio) if segment_ratio < 1 else remaining_pieces)

                                new_cumulative = min(cumulative_pieces[operation_key] + segment_pieces, quantity)

                                if segment_minutes > 0:
                                    batch_schedule.append([
                                        partno, op['operation'], machine_id,
                                        current_segment_start, off_start,
                                        f"Process({new_cumulative}/{quantity}pcs)",
                                        production_order
                                    ])
                                    cumulative_pieces[operation_key] = new_cumulative
                                    remaining_pieces = quantity - new_cumulative
                                    remaining_time -= segment_minutes

                            # Skip the off period
                            current_segment_start = off_end

                        # Process after the last off period until current_end
                        if current_segment_start < current_end:
                            segment_minutes = (current_end - current_segment_start).total_seconds() / 60
                            segment_ratio = segment_minutes / remaining_time if remaining_time > 0 else 1
                            segment_pieces = min(remaining_pieces,
                                                 int(remaining_pieces * segment_ratio) if segment_ratio < 1 else remaining_pieces)

                            new_cumulative = min(cumulative_pieces[operation_key] + segment_pieces, quantity)

                            if segment_minutes > 0:
                                batch_schedule.append([
                                    partno, op['operation'], machine_id,
                                    current_segment_start, current_end,
                                    f"Process({new_cumulative}/{quantity}pcs)",
                                    production_order
                                ])
                                cumulative_pieces[operation_key] = new_cumulative
                                remaining_pieces = quantity - new_cumulative
                                remaining_time -= segment_minutes
                    else:
                        # No OFF periods in this shift
                        shift_completion_ratio = work_possible / remaining_time
                        pieces_this_shift = min(remaining_pieces,
                                                remaining_pieces if work_possible >= remaining_time
                                                else int(remaining_pieces * shift_completion_ratio))

                        new_cumulative = min(cumulative_pieces[operation_key] + pieces_this_shift, quantity)

                        batch_schedule.append([
                            partno, op['operation'], machine_id,
                            next_start, current_end,
                            f"Process({new_cumulative}/{quantity}pcs)",
                            production_order
                        ])

                        cumulative_pieces[operation_key] = new_cumulative
                        remaining_pieces = quantity - new_cumulative
                        remaining_time -= work_possible

                    if remaining_time > 0 and remaining_pieces > 0:
                        next_start = get_next_shift_start(current_shift_end)

                    current_time = current_end
                    machine_end_times[machine_id] = current_end
            else:
                # Check for machine OFF periods within single shift
                off_periods = find_machine_off_periods(machine_id, operation_start, processing_end)

                if off_periods:
                    # Handle each segment separately
                    current_segment_start = operation_start
                    remaining_minutes = (processing_end - operation_start).total_seconds() / 60
                    pieces_processed = 0

                    for off_start, off_end in off_periods:
                        # Process until off_start
                        if current_segment_start < off_start:
                            segment_minutes = (off_start - current_segment_start).total_seconds() / 60
                            segment_ratio = segment_minutes / total_processing_time if total_processing_time > 0 else 0
                            segment_pieces = int(quantity * segment_ratio)

                            new_cumulative = min(cumulative_pieces[operation_key] + segment_pieces, quantity)

                            if segment_minutes > 0:
                                batch_schedule.append([
                                    partno, op['operation'], machine_id,
                                    current_segment_start, off_start,
                                    f"Process({new_cumulative}/{quantity}pcs)",
                                    production_order
                                ])
                                cumulative_pieces[operation_key] = new_cumulative
                                pieces_processed += segment_pieces

                        # Skip the off period
                        current_segment_start = off_end

                    # Process after the last off period until end
                    if current_segment_start < processing_end:
                        segment_minutes = (processing_end - current_segment_start).total_seconds() / 60
                        segment_ratio = segment_minutes / total_processing_time if total_processing_time > 0 else 0
                        segment_pieces = quantity - pieces_processed  # Remaining pieces

                        new_cumulative = min(cumulative_pieces[operation_key] + segment_pieces, quantity)

                        if segment_minutes > 0:
                            batch_schedule.append([
                                partno, op['operation'], machine_id,
                                current_segment_start, processing_end,
                                f"Process({new_cumulative}/{quantity}pcs)",
                                production_order
                            ])
                            cumulative_pieces[operation_key] = new_cumulative

                    current_time = processing_end
                    machine_end_times[machine_id] = processing_end
                else:
                    # No OFF periods, process normally
                    cumulative_pieces[operation_key] = quantity
                    batch_schedule.append([
                        partno, op['operation'], machine_id,
                        operation_start, processing_end,
                        f"Process({quantity}/{quantity}pcs)",
                        production_order
                    ])
                    current_time = processing_end
                    machine_end_times[machine_id] = processing_end

            if op_idx == len(available_operations) - 1:
                for unit_number in range(1, quantity + 1):
                    unit_completion_times[unit_number] = current_time

            operation_time = max(machine_end_times[machine_id], operation_time)

        return batch_schedule, len(available_operations), unit_completion_times

    # Create sorted schedule items for each production order
    sorted_schedule_items = []
    for (partno, production_order) in sorted_parts:
        if (partno, production_order) not in part_operations or partno not in production_orders:
            continue

        # Schedule each production order separately
        for order_info in production_orders[partno]:
            # Match the production order
            if order_info['production_order'] == production_order:
                # Only schedule active parts
                if (partno, production_order) in active_parts:
                    # Create a scheduling item for this production order
                    sorted_schedule_items.append({
                        'partno': partno,
                        'production_order': production_order,
                        'order_id': order_info['order_id'],
                        'priority': order_info['priority'],
                        'lead_time': order_info['lead_time'],
                        'operations': part_operations[(partno, production_order)],
                        'quantity': active_parts[(partno, production_order)],
                        'raw_material': order_info['raw_material']
                    })

    # Sort the schedule items by priority
    sorted_schedule_items.sort(key=lambda x: x['priority'])

    # Main scheduling loop using the sorted items with production orders
    if sorted_schedule_items:
        for schedule_item in sorted_schedule_items:
            partno = schedule_item['partno']
            operations = schedule_item['operations']
            quantity = schedule_item['quantity']
            priority = schedule_item['priority']
            lead_time = schedule_item['lead_time']
            production_order = schedule_item['production_order']
            order_id = schedule_item['order_id']
            raw_material = schedule_item['raw_material']

            # Use part-specific activation time if available, otherwise use global start date
            part_key = (partno, production_order)
            part_start_time = part_activation_times.get(part_key, global_start_date)
            part_start_time = adjust_to_shift_hours(part_start_time)

            # print(
            #     f"Scheduling {partno} - Production Order: {production_order} with activation time (IST): {part_start_time.strftime('%Y-%m-%d %H:%M:%S')} ({part_start_time.strftime('%A')})")

            # Pass order-specific information to schedule_batch_operations
            batch_schedule, completed_ops, unit_completion_times = schedule_batch_operations(
                partno, operations, quantity, part_start_time, order_id, production_order, raw_material
            )

            if batch_schedule:
                schedule.extend(batch_schedule)
                latest_completion_time = max(unit_completion_times.values()) if unit_completion_times else None

                # Track part status for each production order
                status_key = f"{partno}_{production_order}"
                part_status[status_key] = {
                    'partno': partno,
                    'production_order': production_order,
                    'scheduled_end_time': latest_completion_time,
                    'priority': priority,
                    'lead_time': lead_time,
                    'completed_quantity': len(unit_completion_times),
                    'total_quantity': quantity,
                    'lead_time_provided': lead_time is not None,
                    'start_time': part_start_time
                }

                # Calculate lead time difference for monitoring
                if lead_time and latest_completion_time:
                    time_difference = (lead_time - latest_completion_time).days
                    part_status[status_key]['lead_time_difference'] = time_difference

                # Update daily production tracking (only for working days)
                if partno not in daily_production:
                    daily_production[partno] = {}

                for unit_num, completion_time in unit_completion_times.items():
                    completion_day = completion_time.date()
                    # Only track production on working days
                    if is_working_day(completion_time):
                        if completion_day not in daily_production[partno]:
                            daily_production[partno][completion_day] = 0
                        daily_production[partno][completion_day] += 1

                if completed_ops < len(operations):
                    partially_completed.append(
                        f"{partno} (PO: {production_order}): Completed {completed_ops}/{len(operations)} operation types for {quantity} units")
    else:
        # Fallback to original scheduling logic if no schedule items were created
        for (partno, production_order) in sorted_parts:
            if (partno, production_order) not in part_operations:
                continue

            operations = part_operations[(partno, production_order)]
            quantity = active_parts[(partno, production_order)]
            priority = part_priorities.get((partno, production_order), float('inf'))
            lead_time = part_lead_times.get((partno, production_order))

            # Use part-specific activation time if available, otherwise use global start date
            part_key = (partno, production_order)
            part_start_time = part_activation_times.get(part_key, global_start_date)
            part_start_time = adjust_to_shift_hours(part_start_time)

            # print(
            #     f"Scheduling {partno} with activation time (IST): {part_start_time.strftime('%Y-%m-%d %H:%M:%S')} ({part_start_time.strftime('%A')})")

            batch_schedule, completed_ops, unit_completion_times = schedule_batch_operations(
                partno, operations, quantity, part_start_time, production_order=production_order
            )

            if batch_schedule:
                schedule.extend(batch_schedule)
                latest_completion_time = max(unit_completion_times.values()) if unit_completion_times else None

                status_key = f"{partno}_{production_order}"
                part_status[status_key] = {
                    'partno': partno,
                    'production_order': production_order,
                    'scheduled_end_time': latest_completion_time,
                    'priority': priority,
                    'lead_time': lead_time,
                    'completed_quantity': len(unit_completion_times),
                    'total_quantity': quantity,
                    'lead_time_provided': lead_time is not None,
                    'start_time': part_start_time
                }

                if lead_time and latest_completion_time:
                    time_difference = (lead_time - latest_completion_time).days
                    part_status[status_key]['lead_time_difference'] = time_difference

                for unit_num, completion_time in unit_completion_times.items():
                    completion_day = completion_time.date()
                    # Only track production on working days
                    if is_working_day(completion_time):
                        if partno not in daily_production:
                            daily_production[partno] = {}
                        if completion_day not in daily_production[partno]:
                            daily_production[partno][completion_day] = 0
                        daily_production[partno][completion_day] += 1

                if completed_ops < len(operations):
                    partially_completed.append(
                        f"{partno} (PO: {production_order}): Completed {completed_ops}/{len(operations)} operation types for {quantity} units")

    # Create schedule dataframe with production order information
    if not schedule:
        return pd.DataFrame(), global_start_date, 0.0, daily_production, {}, partially_completed

    schedule_df = pd.DataFrame(
        schedule,
        columns=["partno", "operation", "machine_id", "start_time", "end_time", "quantity", "production_order"]
    )

    if schedule_df.empty:
        return schedule_df, global_start_date, 0.0, daily_production, {}, partially_completed

    # Verify that all scheduled times are on working days
    invalid_schedules = []
    for idx, row in schedule_df.iterrows():
        start_time = row['start_time']
        end_time = row['end_time']

        if not is_working_day(start_time) or not is_working_day(end_time):
            invalid_schedules.append(f"Row {idx}: {row['partno']} operation on non-working day")

    if invalid_schedules:
        print("WARNING: Found schedules on non-working days:")
        for invalid in invalid_schedules[:5]:  # Show first 5
            print(f"  {invalid}")

    overall_end_time = max(schedule_df['end_time'])
    overall_time = (overall_end_time - global_start_date).total_seconds() / 60

    # Calculate working days between start and end
    working_days_used = 0
    current_date = global_start_date.date()
    end_date = overall_end_time.date()

    while current_date <= end_date:
        if is_working_day(datetime.combine(current_date, datetime.min.time())):
            working_days_used += 1
        current_date += timedelta(days=1)

    # print(f"\nScheduling Summary:")
    # print(f"  Start Date: {global_start_date.strftime('%Y-%m-%d %H:%M:%S')} ({global_start_date.strftime('%A')})")
    # print(f"  End Date: {overall_end_time.strftime('%Y-%m-%d %H:%M:%S')} ({overall_end_time.strftime('%A')})")
    # print(f"  Working Days Used: {working_days_used}")
    # print(f"  Total Schedule Duration: {overall_time:.1f} minutes")

    # PDC computation is now only done on non-default machines (which are already filtered out)
    try:
        # Group by part and production order to find latest end time = PDC
        part_production_end_times = (
            schedule_df.groupby(['partno', 'production_order'])['end_time'].max()
        )

        for (part_number, production_order), pdc_time in part_production_end_times.items():
            try:
                order = Order.get(production_order=production_order)
                if not order:
                    print(f"Order not found for production_order: {production_order}")
                    continue

                upsert_pdc_record(
                    order_id=order.id,
                    part_number=part_number,
                    production_order=production_order,
                    pdc_data=pdc_time,
                    data_source='scheduled',
                    is_active=True,
                )
                # print(f"Upserted PDC: {part_number} - {production_order} -> {pdc_time} ({pdc_time.strftime('%A')})")
            except Exception as e:
                print(f"Error storing PDC for {part_number} - {production_order}: {str(e)}")
                continue
    except Exception as e:
        print(f"Error computing/storing PDCs from schedule: {str(e)}")

    return schedule_df, overall_end_time, overall_time, daily_production, part_status, partially_completed