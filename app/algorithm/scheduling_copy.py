################## machine utilization code ###############################

from datetime import datetime, timedelta, date
import pandas as pd
from typing import Dict, Tuple, List
from decimal import Decimal
from pony.orm import select, db_session
from app.models import Operation, Order, Machine, Status, RawMaterial, Project, InventoryStatus, MachineStatus, \
    PartScheduleStatus


def adjust_to_shift_hours(time: datetime) -> datetime:
    """Adjust time to fit within shift hours (9 AM to 5 PM)"""
    if time.hour < 9:
        return time.replace(hour=9, minute=0, second=0, microsecond=0)
    elif time.hour >= 17:
        return (time + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    return time


@db_session
def schedule_operations(df: pd.DataFrame, component_quantities: Dict[str, int],
                        lead_times: Dict[str, datetime] = None) -> \
        Tuple[pd.DataFrame, datetime, float, Dict, Dict, List[str]]:
    """Main scheduling function that creates a production schedule based on operations data"""

    if df.empty:
        return pd.DataFrame(), datetime.now(), 0.0, {}, {}, []

    # Gather all order and part status information upfront
    part_status_map = {}
    for part_status in PartScheduleStatus.select():
        part_status_map[part_status.part_number] = part_status.status

    # Filter quantities to only include active parts
    active_parts = {
        partno: qty
        for partno, qty in component_quantities.items()
        if part_status_map.get(partno, 'inactive') == 'active'
    }

    # Debug print the active parts
    print("\n--- Active Parts ---")
    for partno, qty in active_parts.items():
        print(f"Part: {partno}, Quantity: {qty}")

    # Track skipped parts
    skipped_parts = [
        f"Skipped {partno}: Status inactive"
        for partno in component_quantities.keys()
        if part_status_map.get(partno, 'inactive') != 'active'
    ]

    if not active_parts:
        return pd.DataFrame(), datetime.now(), 0.0, {}, {}, ["No parts are marked as active for scheduling"]

    # Get project priorities and lead times
    part_priorities = {}
    part_lead_times = {}
    order_info = {}

    # Gather all order, priority, and lead time information
    for partno in active_parts.keys():
        order = Order.select(lambda o: o.part_number == partno).first()
        if order:
            project_priority = order.project.priority if order.project else float('inf')
            lead_time = order.project.delivery_date if order.project else None

            part_priorities[partno] = project_priority
            part_lead_times[partno] = lead_time
            order_info[partno] = {
                'priority': project_priority,
                'lead_time': lead_time,  # Store lead time for reference
                'order_id': order.id
            }

    # Sort parts by priority only (lower number = higher priority)
    sorted_parts = sorted(
        active_parts.keys(),
        key=lambda x: (part_priorities.get(x, float('inf')))
    )

    # Reorder the dataframe based on sorted parts
    df['sort_order'] = df['partno'].map({part: idx for idx, part in enumerate(sorted_parts)})
    df_sorted = df[df['partno'].isin(active_parts)].sort_values(by=['sort_order', 'sequence']).drop('sort_order',
                                                                                                    axis=1)

    # Fetch raw materials with their InventoryStatus
    raw_materials_query = select((o.part_number, o.raw_material, ist)
                                 for o in Order
                                 for ist in InventoryStatus
                                 if o.raw_material and o.raw_material.status == ist)
    raw_materials = {
        part_number: (
            ist.name == 'Available',
            rm.quantity,
            rm.unit,
            rm.available_from
        ) for part_number, rm, ist in raw_materials_query
    }

    # Fetch machine statuses with their status details
    machine_statuses_query = select((m, ms, s) for m in Machine
                                    for ms in m.status
                                    for s in Status if ms.status == s)
    machine_statuses = {
        m.id: {
            'machine_make': m.make,
            'status_id': s.id,
            'status_name': s.name,
            'status_description': s.description,
            'machine_status_description': ms.description,
            'available_from': ms.available_from
        } for m, ms, s in machine_statuses_query
    }

    part_operations = {
        partno: group.to_dict('records')
        for partno, group in df_sorted.groupby('partno')
    }

    start_date = datetime(2024, 12, 20, 9, 0)
    start_date = adjust_to_shift_hours(start_date)

    schedule = []
    machine_end_times = {machine: start_date for machine in df_sorted["machine_id"].unique()}
    daily_production = {}
    part_status = {}
    partially_completed = []

    # Modified: Track machine schedules to find optimal slots
    machine_schedules = {machine: [] for machine in df_sorted["machine_id"].unique()}

    def check_machine_status(machine_id: int, time: datetime) -> Tuple[bool, datetime]:
        """Check if a machine is available at a given time"""
        machine_status = machine_statuses.get(machine_id, {})

        print(f"\nChecking Machine ID: {machine_id}")
        print(f"Machine Status Details: {machine_status}")

        if not machine_status:
            print(f"No status found for Machine ID {machine_id}. Assuming unavailable.")
            return False, None

        if machine_status.get('status_name', '').upper() == 'OFF':
            print(f"Machine {machine_id} is OFF")
            return False, None

        available_from = machine_status.get('available_from')
        if available_from and time < available_from:
            print(f"Machine {machine_id} not available before {available_from}")
            return False, available_from

        return True, time

    def find_last_available_operation(operations: List[dict], current_time: datetime) -> int:
        """Find the last operation that can be performed in sequence"""
        last_available = -1
        current_op_time = current_time

        for idx, op in enumerate(operations):
            machine_id = op['machine_id']
            machine_available, available_time = check_machine_status(machine_id, current_op_time)

            if not machine_available and available_time is None:
                break

            if available_time:
                current_op_time = available_time

            last_available = idx
            op_time = float(op['time']) * 60
            current_op_time += timedelta(minutes=op_time)

        return last_available

    def find_optimal_machine_slot(machine_id: int, duration_minutes: float, earliest_start: datetime) -> datetime:
        """Find the best time slot for an operation on a machine considering utilization"""
        earliest_start = adjust_to_shift_hours(earliest_start)

        # Check machine availability
        machine_available, available_time = check_machine_status(machine_id, earliest_start)
        if not machine_available:
            if available_time is None:
                return None  # Machine is permanently unavailable
            earliest_start = adjust_to_shift_hours(available_time)

        # Get machine schedule
        schedule = machine_schedules.get(machine_id, [])

        # If no existing schedule, use earliest_start
        if not schedule:
            return earliest_start

        # Sort schedule by start_time
        schedule.sort(key=lambda x: x['start_time'])

        # Check for gaps between operations where this operation could fit
        for i in range(len(schedule)):
            current = schedule[i]

            # If this is the first operation and there's room before it
            if i == 0 and earliest_start < current['start_time']:
                gap_duration = (current['start_time'] - earliest_start).total_seconds() / 60
                if gap_duration >= duration_minutes:
                    return earliest_start

            # If this isn't the last operation, check gap to next operation
            if i < len(schedule) - 1:
                next_op = schedule[i + 1]
                gap_start = max(current['end_time'], earliest_start)
                gap_duration = (next_op['start_time'] - gap_start).total_seconds() / 60

                if gap_duration >= duration_minutes:
                    return gap_start

        # If no suitable gap found, schedule after the last operation
        if schedule:
            last_end = max(op['end_time'] for op in schedule)
            return max(last_end, earliest_start)
        else:
            return earliest_start

    def schedule_batch_operations(partno: str, operations: List[dict], quantity: int, start_time: datetime) -> Tuple[
        List[list], int, Dict[int, datetime]]:
        """Schedule operations for a batch of components with improved machine utilization"""

        batch_schedule = []
        operation_time = start_time
        unit_completion_times = {}
        cumulative_pieces = {}
        operation_setup_done = {}

        # Check raw material availability
        order = Order.get(part_number=partno)
        if not order or not order.raw_material:
            return [], 0, {}

        raw_material_status = order.raw_material.status
        raw_available = raw_material_status.name == 'Available'
        raw_available_time = order.raw_material.available_from

        if not raw_available:
            return [], 0, {}

        if raw_available_time and operation_time < raw_available_time:
            operation_time = raw_available_time

        last_available_idx = find_last_available_operation(operations, operation_time)
        if last_available_idx < 0:
            return [], 0, {}

        available_operations = operations[:last_available_idx + 1]

        # Track completion time of previous operation for sequencing
        prev_op_end_time = None

        for op_idx, op in enumerate(available_operations):
            machine_id = op['machine_id']
            operation_key = f"{op['operation']}_{machine_id}"

            if operation_key not in cumulative_pieces:
                cumulative_pieces[operation_key] = 0
                operation_setup_done[operation_key] = False

            # Get setup time and cycle time
            operation = Operation.select(lambda o:
                                         o.order.part_number == partno and
                                         o.operation_number == op['sequence']).first()

            if not operation:
                continue

            setup_minutes = float(operation.setup_time) * 60
            cycle_minutes = float(operation.ideal_cycle_time) * 60
            total_op_minutes = setup_minutes + (cycle_minutes * quantity)

            # Determine earliest possible start time (respecting operation sequence)
            if prev_op_end_time is not None:
                # Must wait for previous operation to complete
                earliest_start = prev_op_end_time
            else:
                # First operation can start at operation_time
                earliest_start = operation_time

            # Find optimal slot for this operation on this machine
            current_time = find_optimal_machine_slot(machine_id, total_op_minutes, earliest_start)

            if current_time is None:
                continue  # Skip this operation if machine not available

            operation_start = current_time

            # Handle setup time
            if not operation_setup_done[operation_key]:
                setup_end = operation_start + timedelta(minutes=setup_minutes)
                shift_end = operation_start.replace(hour=17, minute=0, second=0, microsecond=0)

                if setup_end > shift_end:
                    batch_schedule.append([
                        partno, op['operation'], machine_id,
                        operation_start, shift_end,
                        f"Setup({int((shift_end - operation_start).total_seconds() / 60)}/{setup_minutes}min)"
                    ])

                    # Add to machine schedule
                    machine_schedules[machine_id].append({
                        'partno': partno,
                        'operation': op['operation'],
                        'start_time': operation_start,
                        'end_time': shift_end,
                        'type': 'setup'
                    })

                    next_day = shift_end + timedelta(days=1)
                    next_start = next_day.replace(hour=9, minute=0, second=0, microsecond=0)
                    remaining_setup = setup_minutes - (shift_end - operation_start).total_seconds() / 60

                    while remaining_setup > 0:
                        current_shift_end = next_start.replace(hour=17, minute=0, second=0, microsecond=0)
                        setup_possible = min(remaining_setup, (current_shift_end - next_start).total_seconds() / 60)
                        current_end = next_start + timedelta(minutes=setup_possible)

                        batch_schedule.append([
                            partno, op['operation'], machine_id,
                            next_start, current_end,
                            f"Setup({setup_minutes - remaining_setup + setup_possible}/{setup_minutes}min)"
                        ])

                        # Add to machine schedule
                        machine_schedules[machine_id].append({
                            'partno': partno,
                            'operation': op['operation'],
                            'start_time': next_start,
                            'end_time': current_end,
                            'type': 'setup'
                        })

                        remaining_setup -= setup_possible
                        if remaining_setup > 0:
                            next_start = (current_shift_end + timedelta(days=1)).replace(hour=9, minute=0, second=0,
                                                                                         microsecond=0)

                        current_time = current_end
                    operation_start = current_end
                else:
                    batch_schedule.append([
                        partno, op['operation'], machine_id,
                        operation_start, setup_end,
                        f"Setup({setup_minutes}/{setup_minutes}min)"
                    ])

                    # Add to machine schedule
                    machine_schedules[machine_id].append({
                        'partno': partno,
                        'operation': op['operation'],
                        'start_time': operation_start,
                        'end_time': setup_end,
                        'type': 'setup'
                    })

                    operation_start = setup_end
                    current_time = setup_end

                operation_setup_done[operation_key] = True

            # Process production
            total_processing_time = cycle_minutes * quantity
            processing_end = operation_start + timedelta(minutes=total_processing_time)
            shift_end = operation_start.replace(hour=17, minute=0, second=0, microsecond=0)

            if processing_end > shift_end:
                # Split processing across shifts
                work_minutes_today = (shift_end - operation_start).total_seconds() / 60
                completion_ratio = work_minutes_today / total_processing_time if total_processing_time > 0 else 0
                pieces_today = int(quantity * completion_ratio)

                new_cumulative = min(cumulative_pieces[operation_key] + pieces_today, quantity)
                if work_minutes_today > 0:
                    batch_schedule.append([
                        partno, op['operation'], machine_id,
                        operation_start, shift_end,
                        f"Process({new_cumulative}/{quantity}pcs)"
                    ])

                    # Add to machine schedule
                    machine_schedules[machine_id].append({
                        'partno': partno,
                        'operation': op['operation'],
                        'start_time': operation_start,
                        'end_time': shift_end,
                        'type': 'process'
                    })

                    cumulative_pieces[operation_key] = new_cumulative

                remaining_time = total_processing_time - work_minutes_today
                remaining_pieces = quantity - new_cumulative

                next_day = shift_end + timedelta(days=1)
                next_start = next_day.replace(hour=9, minute=0, second=0, microsecond=0)

                while remaining_time > 0:
                    current_shift_end = next_start.replace(hour=17, minute=0, second=0, microsecond=0)
                    work_possible = min(remaining_time, (current_shift_end - next_start).total_seconds() / 60)
                    current_end = next_start + timedelta(minutes=work_possible)

                    shift_completion_ratio = work_possible / remaining_time
                    pieces_this_shift = min(remaining_pieces,
                                            remaining_pieces if work_possible >= remaining_time
                                            else int(remaining_pieces * shift_completion_ratio))

                    new_cumulative = min(cumulative_pieces[operation_key] + pieces_this_shift, quantity)

                    batch_schedule.append([
                        partno, op['operation'], machine_id,
                        next_start, current_end,
                        f"Process({new_cumulative}/{quantity}pcs)"
                    ])

                    # Add to machine schedule
                    machine_schedules[machine_id].append({
                        'partno': partno,
                        'operation': op['operation'],
                        'start_time': next_start,
                        'end_time': current_end,
                        'type': 'process'
                    })

                    cumulative_pieces[operation_key] = new_cumulative
                    remaining_pieces = quantity - new_cumulative
                    remaining_time -= work_possible

                    if remaining_time > 0:
                        next_start = (current_shift_end + timedelta(days=1)).replace(hour=9, minute=0, second=0,
                                                                                     microsecond=0)

                    current_time = current_end
                    machine_end_times[machine_id] = current_end
            else:
                cumulative_pieces[operation_key] = quantity
                batch_schedule.append([
                    partno, op['operation'], machine_id,
                    operation_start, processing_end,
                    f"Process({quantity}/{quantity}pcs)"
                ])

                # Add to machine schedule
                machine_schedules[machine_id].append({
                    'partno': partno,
                    'operation': op['operation'],
                    'start_time': operation_start,
                    'end_time': processing_end,
                    'type': 'process'
                })

                current_time = processing_end
                machine_end_times[machine_id] = processing_end

            # Update previous operation end time for next operation's sequencing
            prev_op_end_time = current_time

            if op_idx == len(available_operations) - 1:
                for unit_number in range(1, quantity + 1):
                    unit_completion_times[unit_number] = current_time

        return batch_schedule, len(available_operations), unit_completion_times

    # Main scheduling loop using sorted_parts
    for partno in sorted_parts:
        if partno not in part_operations:
            continue

        operations = part_operations[partno]
        quantity = component_quantities[partno]
        priority = part_priorities.get(partno, float('inf'))
        lead_time = part_lead_times.get(partno)  # Get lead time for reference

        batch_schedule, completed_ops, unit_completion_times = schedule_batch_operations(
            partno, operations, quantity, start_date
        )

        if batch_schedule:
            schedule.extend(batch_schedule)
            latest_completion_time = max(unit_completion_times.values()) if unit_completion_times else None

            part_status[partno] = {
                'partno': partno,
                'scheduled_end_time': latest_completion_time,
                'priority': priority,
                'lead_time': lead_time,  # Include lead time in status for reference
                'completed_quantity': len(unit_completion_times),
                'total_quantity': quantity,
                'lead_time_provided': lead_time is not None  # Track if lead time was provided
            }

            # Calculate lead time difference for monitoring (but don't use for scheduling)
            if lead_time and latest_completion_time:
                time_difference = (lead_time - latest_completion_time).days
                part_status[partno]['lead_time_difference'] = time_difference  # Positive means ahead of lead time

            # Update daily production tracking
            for unit_num, completion_time in unit_completion_times.items():
                completion_day = completion_time.date()
                if partno not in daily_production:
                    daily_production[partno] = {}
                if completion_day not in daily_production[partno]:
                    daily_production[partno][completion_day] = 0
                daily_production[partno][completion_day] += 1

            if completed_ops < len(operations):
                partially_completed.append(
                    f"{partno}: Completed {completed_ops}/{len(operations)} operation types for {quantity} units")

    schedule_df = pd.DataFrame(
        schedule,
        columns=["partno", "operation", "machine_id", "start_time", "end_time", "quantity"]
    )

    if schedule_df.empty:
        return schedule_df, start_date, 0.0, daily_production, {}, partially_completed

    overall_end_time = max(schedule_df['end_time'])
    overall_time = (overall_end_time - start_date).total_seconds() / 60

    return schedule_df, overall_end_time, overall_time, daily_production, part_status, partially_completed

