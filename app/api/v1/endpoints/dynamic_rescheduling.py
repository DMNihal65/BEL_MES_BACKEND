from fastapi import APIRouter, HTTPException
from pony.orm import db_session, select, desc
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from app.algorithm.scheduling import adjust_to_shift_hours, schedule_operations
from app.crud.component_quantities import fetch_component_quantities
from app.crud.leadtime import fetch_lead_times
from app.crud.operation import fetch_operations
from app.models import PlannedScheduleItem, ScheduleVersion, ProductionLog, Order, Operation, Status
from app.schemas.scheduled1 import CombinedScheduleResponse, WorkCenterInfo
from app.models.master_order import WorkCenter, MachineStatus, Machine
from app.schemas.scheduled1 import ProductionLogResponse, ScheduledOperation
import re
import hashlib

router = APIRouter(prefix="/api/v1/rescheduling", tags=["rescheduling"])


def adjust_to_shift_hours(time: datetime) -> datetime:
    """Adjust time to fit within shift hours (6 AM to 5 PM)"""
    if time.hour < 6:
        return time.replace(hour=6, minute=0, second=0, microsecond=0)
    elif time.hour >= 22:
        return (time + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    return time


def calculate_shift_aware_duration(start_time: datetime, operation: Operation, quantity: int) -> Tuple[
    datetime, timedelta]:
    """
    Calculate the end time and duration for an operation, respecting shift hours (6 AM to 5 PM)
    """
    setup_time = float(operation.setup_time) * 60  # Convert to minutes
    cycle_time = float(operation.ideal_cycle_time) * 60  # Convert to minutes
    total_minutes = setup_time + (cycle_time * quantity)

    current_time = adjust_to_shift_hours(start_time)
    remaining_minutes = total_minutes
    shift_start_hour = 6
    shift_end_hour = 22
    shift_minutes_per_day = (shift_end_hour - shift_start_hour) * 60  # 480 minutes

    while remaining_minutes > 0:
        current_hour = current_time.hour
        current_minute = current_time.minute
        minutes_until_shift_end = ((shift_end_hour - current_hour) * 60) - current_minute

        if minutes_until_shift_end <= 0:
            current_time = (current_time + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
            continue

        minutes_to_allocate = min(remaining_minutes, minutes_until_shift_end)
        remaining_minutes -= minutes_to_allocate
        current_time += timedelta(minutes=minutes_to_allocate)

        if remaining_minutes > 0:
            current_time = (current_time + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)

    end_time = current_time
    total_duration = timedelta(minutes=total_minutes)
    return end_time, total_duration


def check_machine_status(machine_id: int, time: datetime) -> Tuple[bool, datetime]:
    """Check if a machine is available at a given time using algorithm logic"""
    with db_session:
        machine_status = select((ms, s) for ms in MachineStatus
                                for s in Status
                                if ms.machine.id == machine_id and
                                ms.status == s).first()

        if not machine_status:
            return False, None

        ms, status = machine_status
        if status.name.upper() == 'OFF':
            return False, None

        if ms.available_from and time < ms.available_from:
            return False, ms.available_from

        return True, time


def find_last_available_operation(operations: List[dict], current_time: datetime) -> int:
    """Find the last operation that can be performed in sequence"""
    last_available = -1
    current_op_time = current_time

    for idx, op in enumerate(operations):
        machine_id = op.machine.id
        machine_available, available_time = check_machine_status(machine_id, current_op_time)

        if not machine_available and available_time is None:
            break

        if available_time:
            current_op_time = available_time

        last_available = idx
        setup_time = float(op.setup_time) * 60
        cycle_time = float(op.ideal_cycle_time) * 60
        current_op_time += timedelta(minutes=(setup_time + cycle_time))

    return last_available


def check_raw_material_status(order: Order, time: datetime) -> Tuple[bool, datetime]:
    """Check raw material availability"""
    if not order or not order.raw_material:
        return False, None

    raw_material_status = order.raw_material.status
    raw_available = raw_material_status.name == 'Available'
    raw_available_time = order.raw_material.available_from

    if not raw_available:
        return False, None

    if raw_available_time and time < raw_available_time:
        return True, raw_available_time

    return True, time


def calculate_operation_delay(actual_end_time: datetime, planned_end_time: datetime) -> Optional[timedelta]:
    """
    Calculate the delay between actual and planned end times
    """
    if actual_end_time > planned_end_time:
        return actual_end_time - planned_end_time
    return None


def is_machine_in_schedulable_work_center(machine_id: int) -> bool:
    """
    Check if a machine belongs to a work center that is schedulable
    """
    with db_session:
        machine = Machine.get(id=machine_id)
        if not machine or not hasattr(machine, 'work_center') or not machine.work_center:
            return False
        return machine.work_center.is_schedulable


def propagate_delay_to_dependent_operations(part_number: str, completed_operation_number: int,
                                            actual_end_time: datetime, completed_qty: int, total_qty: int):
    """
    Propagate scheduling changes to operations that depend on the completed operation, considering quantities
    Only reschedule operations in work centers that are marked as schedulable
    """
    updated_items = []

    with db_session:
        orders = select(o for o in Order if o.part_number == part_number).order_by(lambda o: desc(o.id))
        order = orders.first()

        if not order:
            print(f"Order not found for part number: {part_number}")
            return updated_items

        operations = list(
            select(op for op in Operation if op.order == order).order_by(lambda op: op.operation_number)[:])

        completed_op_index = -1
        for i, op in enumerate(operations):
            if op.operation_number == completed_operation_number:
                completed_op_index = i
                break

        if completed_op_index == -1 or completed_op_index == len(operations) - 1:
            print(f"Operation {completed_operation_number} not found or is the last operation in the sequence")
            return updated_items

        current_start_time = adjust_to_shift_hours(actual_end_time)
        remaining_qty = total_qty - completed_qty

        for i in range(completed_op_index + 1, len(operations)):
            dependent_op = operations[i]

            # Skip operations that are not in schedulable work centers
            if not hasattr(dependent_op, 'machine') or not dependent_op.machine:
                continue

            if not is_machine_in_schedulable_work_center(dependent_op.machine.id):
                print(f"Skipping operation {dependent_op.operation_number} as it's in a non-schedulable work center")
                continue

            schedule_items = list(select(item for item in PlannedScheduleItem
                                         if item.operation == dependent_op)[:])

            if not schedule_items:
                print(f"No scheduled items found for operation {dependent_op.operation_number}")
                continue

            schedule_items.sort(key=lambda x: x.id, reverse=True)
            latest_item = schedule_items[0]

            current_version = select(v for v in ScheduleVersion
                                     if v.schedule_item == latest_item and
                                     v.is_active == True).first()

            if not current_version:
                print(f"No active version found for operation {dependent_op.operation_number}")
                continue

            new_end_time, operation_duration = calculate_shift_aware_duration(current_start_time, dependent_op,
                                                                              total_qty)
            new_start_time = adjust_to_shift_hours(current_start_time)

            new_version_number = current_version.version_number + 1
            new_version = ScheduleVersion(
                schedule_item=latest_item,
                version_number=new_version_number,
                planned_start_time=new_start_time,
                planned_end_time=new_end_time,
                planned_quantity=total_qty,
                completed_quantity=0,
                remaining_quantity=total_qty,
                is_active=True,
                created_at=datetime.utcnow()
            )

            current_version.is_active = False
            latest_item.current_version = new_version_number
            latest_item.remaining_quantity = total_qty

            dependent_ops = select(o for o in Operation
                                   if o.order == latest_item.order
                                   ).order_by(lambda o: o.operation_number)[:]
            last_available_idx = find_last_available_operation(list(dependent_ops), new_start_time)

            updated_items.append({
                'item_id': latest_item.id,
                'operation_id': dependent_op.id,
                'old_version': current_version.version_number,
                'new_version': new_version_number,
                'completed_qty': 0,
                'remaining_qty': total_qty,
                'start_time': new_start_time.isoformat(),
                'end_time': new_end_time.isoformat(),
                'machine_id': latest_item.machine.id,
                'raw_material_status': 'Available',
                'operation_number': dependent_op.operation_number,
                'last_available_operation': last_available_idx,
                'part_number': part_number,
                'production_order': order.production_order
            })

            current_start_time = new_end_time

    return updated_items


def determine_work_center_schedulability(work_center: WorkCenter) -> bool:
    """
    Determine if a work center is schedulable based on machine availability
    and scheduled operations
    """
    with db_session:
        # First check if the work center is explicitly marked as schedulable
        if not work_center.is_schedulable:
            return False

        # Check if the work center has at least one machine
        if not work_center.machines:
            return False

        # Check if at least one machine in the work center is available
        for machine in work_center.machines:
            machine_status = select(ms for ms in MachineStatus
                                    if ms.machine == machine).first()

            if machine_status:
                status = machine_status.status
                if status and status.name.upper() != 'OFF':
                    # Check if there are any scheduled operations for this machine
                    scheduled_items = select(p for p in PlannedScheduleItem
                                             if p.machine == machine).first()

                    if scheduled_items:
                        return True

        return False


@router.post("/dynamic-reschedule")


async def dynamic_reschedule():
    """Dynamically reschedule operations based on production logs with improved handling of is_schedulable flag"""
    try:
        with db_session:
            # Build machine schedulability lookup dict first - OPTIMIZED with prefetch
            machine_schedulability = {}
            work_centers = select(wc for wc in WorkCenter).prefetch(WorkCenter.machines)[:]
            for work_center in work_centers:
                for machine in work_center.machines:
                    machine_schedulability[machine.id] = work_center.is_schedulable

            # OPTIMIZED: Use prefetch to reduce N+1 queries
            schedule_items = select(p for p in PlannedScheduleItem).prefetch(
                PlannedScheduleItem.operation,
                PlannedScheduleItem.machine,
                PlannedScheduleItem.order
            ).order_by(lambda p: (p.operation.operation_number, p.id))[:]

            if not schedule_items:
                # Return empty response with work center info when no schedule items exist
                empty_work_centers = []
                for work_center in work_centers:
                    machines_in_wc = []
                    for machine in work_center.machines:
                        machines_in_wc.append({
                            "id": str(machine.id),
                            "name": machine.make,
                            "model": machine.model,
                            "type": machine.type
                        })
                    empty_work_centers.append(
                        WorkCenterInfo(
                            work_center_code=work_center.code,
                            work_center_name=work_center.work_center_name or "",
                            machines=machines_in_wc,
                            is_schedulable=work_center.is_schedulable
                        )
                    )
                return CombinedScheduleResponse(
                    reschedule=[],
                    total_updates=0,
                    production_logs=[],
                    scheduled_operations=[],
                    overall_end_time=datetime.now(),
                    overall_time="0",
                    daily_production={},
                    total_completed=0,
                    total_rejected=0,
                    total_logs=0,
                    work_centers=empty_work_centers
                )

            updates = []
            cascade_updates = []
            valid_part_numbers = set()
            grouped_items = {}
            processed_operations = set()

            # OPTIMIZED: Bulk fetch all schedule versions and production logs
            all_versions = select(v for v in ScheduleVersion).prefetch(ScheduleVersion.schedule_item)[:]
            version_by_item = {}
            for version in all_versions:
                if version.schedule_item.id not in version_by_item:
                    version_by_item[version.schedule_item.id] = []
                version_by_item[version.schedule_item.id].append(version)

            all_production_logs = select(l for l in ProductionLog).prefetch(
                ProductionLog.schedule_version,
                ProductionLog.operation
            )[:]

            # Create lookup dictionaries for faster access
            logs_by_version = {}
            logs_by_operation = {}
            for log in all_production_logs:
                if log.schedule_version:
                    if log.schedule_version.id not in logs_by_version:
                        logs_by_version[log.schedule_version.id] = []
                    logs_by_version[log.schedule_version.id].append(log)
                elif log.operation:
                    if log.operation.id not in logs_by_operation:
                        logs_by_operation[log.operation.id] = []
                    logs_by_operation[log.operation.id].append(log)

            # Collect valid part numbers based on production logs - OPTIMIZED
            for item in schedule_items:
                versions = version_by_item.get(item.id, [])
                has_logs = False
                for version in versions:
                    logs = logs_by_version.get(version.id, [])
                    if logs:
                        has_logs = True
                        valid_part_numbers.add(item.order.part_number)
                        break
                if not has_logs:
                    logs = logs_by_operation.get(item.operation.id, [])
                    if logs:
                        valid_part_numbers.add(item.order.part_number)

            # Group items by machine, operation, and part number
            for item in schedule_items:
                if item.order.part_number in valid_part_numbers:
                    key = (item.machine.id, item.operation.operation_number, item.order.part_number)
                    if key not in grouped_items:
                        grouped_items[key] = []
                    grouped_items[key].append(item)

            completed_operations = {}

            # Process each group of items
            for (machine_id, operation_number, part_number), items in grouped_items.items():
                try:
                    # Skip if machine is in a non-schedulable work center
                    if machine_id in machine_schedulability and not machine_schedulability[machine_id]:
                        print(f"Skipping machine {machine_id} as it's in a non-schedulable work center")
                        continue

                    if not items:
                        continue

                    operation_key = (part_number, operation_number)
                    if operation_key in processed_operations:
                        continue

                    items.sort(key=lambda x: x.id)
                    last_item = items[-1]

                    # OPTIMIZED: Use pre-fetched versions
                    item_versions = version_by_item.get(last_item.id, [])
                    current_version = None
                    for v in item_versions:
                        if v.is_active:
                            current_version = v
                            break

                    if not current_version:
                        continue

                    # Get all logs for the operation - OPTIMIZED using pre-built lookups
                    all_operation_logs = []

                    # Get logs that are connected to versions of this item
                    for item in items:
                        item_versions = version_by_item.get(item.id, [])
                        for version in item_versions:
                            logs = logs_by_version.get(version.id, [])
                            all_operation_logs.extend(logs)

                    # Get logs that are connected directly to the operation without version
                    operation_logs_no_version = logs_by_operation.get(last_item.operation.id, [])
                    all_operation_logs.extend(operation_logs_no_version)

                    # Filter out logs without completed quantities
                    valid_logs = [log for log in all_operation_logs if log.quantity_completed is not None]

                    if not valid_logs:
                        continue

                    # Calculate the actual completed quantity from logs
                    actual_completed_qty = sum(log.quantity_completed for log in valid_logs)

                    # Get the total quantity from the schedule item
                    total_qty = last_item.total_quantity

                    # Calculate remaining quantity
                    remaining_qty = max(0, total_qty - actual_completed_qty)

                    # Get valid start and end times from logs
                    valid_start_times = [log.start_time for log in valid_logs if log.start_time is not None]
                    valid_end_times = [log.end_time for log in valid_logs if log.end_time is not None]

                    if not valid_start_times or not valid_end_times:
                        continue

                    group_start_time = min(valid_start_times)
                    group_end_time = max(valid_end_times)

                    if part_number not in completed_operations:
                        completed_operations[part_number] = []

                    operation_id = last_item.operation.id if last_item.operation else last_item.id

                    # OPTIMIZED: Fetch dependent operations once
                    dependent_ops = select(o for o in Operation
                                           if o.order == last_item.order
                                           ).order_by(lambda o: o.operation_number)[:]
                    last_available_idx = find_last_available_operation(list(dependent_ops), group_start_time)

                    # Handle completed or partially completed operations
                    if actual_completed_qty > 0:
                        new_version_number = current_version.version_number + 1
                        new_version = ScheduleVersion(
                            schedule_item=last_item,
                            version_number=new_version_number,
                            planned_start_time=group_start_time,
                            planned_end_time=group_end_time,
                            planned_quantity=actual_completed_qty,
                            completed_quantity=actual_completed_qty,
                            remaining_quantity=0,
                            is_active=True,
                            created_at=datetime.utcnow()
                        )

                        current_version.is_active = False
                        last_item.current_version = new_version_number
                        last_item.remaining_quantity = remaining_qty
                        last_item.status = 'completed' if remaining_qty == 0 else 'scheduled'

                        updates.append({
                            'item_id': last_item.id,
                            'operation_id': operation_id,
                            'old_version': current_version.version_number,
                            'new_version': new_version_number,
                            'completed_qty': actual_completed_qty,
                            'remaining_qty': remaining_qty,
                            'start_time': group_start_time.isoformat(),
                            'end_time': group_end_time.isoformat(),
                            'machine_id': machine_id,
                            'raw_material_status': 'Available',
                            'operation_number': operation_number,
                            'last_available_operation': last_available_idx,
                            'part_number': part_number,
                            'production_order': last_item.order.production_order
                        })

                    # Schedule remaining quantity if any
                    if remaining_qty > 0:
                        remaining_start_time = adjust_to_shift_hours(group_end_time)
                        remaining_end_time, _ = calculate_shift_aware_duration(remaining_start_time,
                                                                               last_item.operation, remaining_qty)

                        new_version_number = (
                                                 new_version.version_number if actual_completed_qty > 0 else current_version.version_number) + 1
                        new_version = ScheduleVersion(
                            schedule_item=last_item,
                            version_number=new_version_number,
                            planned_start_time=remaining_start_time,
                            planned_end_time=remaining_end_time,
                            planned_quantity=remaining_qty,
                            completed_quantity=0,
                            remaining_quantity=remaining_qty,
                            is_active=True,
                            created_at=datetime.utcnow()
                        )

                        if actual_completed_qty > 0:
                            # If there was a completed portion, the previous new_version is already created
                            pass
                        else:
                            current_version.is_active = False
                        last_item.current_version = new_version_number
                        last_item.remaining_quantity = remaining_qty
                        last_item.status = 'scheduled'

                        last_available_idx = find_last_available_operation(list(dependent_ops), remaining_start_time)

                        updates.append({
                            'item_id': last_item.id,
                            'operation_id': operation_id,
                            'old_version': new_version_number - 1,
                            'new_version': new_version_number,
                            'completed_qty': 0,
                            'remaining_qty': remaining_qty,
                            'start_time': remaining_start_time.isoformat(),
                            'end_time': remaining_end_time.isoformat(),
                            'machine_id': machine_id,
                            'raw_material_status': 'Available',
                            'operation_number': operation_number,
                            'last_available_operation': last_available_idx,
                            'part_number': part_number,
                            'production_order': last_item.order.production_order
                        })

                        group_end_time = remaining_end_time

                    completed_operations[part_number].append(
                        (operation_number, group_end_time, actual_completed_qty, total_qty))
                    processed_operations.add(operation_key)

                except Exception as group_error:
                    print(
                        f"Error processing group for machine {machine_id}, operation {operation_number}: {str(group_error)}")
                    continue

            # Propagate delays to dependent operations, but only for schedulable work centers
            for part_number, operations in completed_operations.items():
                operations.sort(key=lambda x: x[0])
                max_end_time = max(op[1] for op in operations)
                max_op_num = max(op[0] for op in operations)
                completed_qty = max(op[2] for op in operations)
                total_qty = max(op[3] for op in operations)
                cascade_results = propagate_delay_to_dependent_operations(
                    part_number, max_op_num, max_end_time, completed_qty, total_qty)
                if cascade_results:
                    cascade_updates.extend(cascade_results)

            all_updates = updates + cascade_updates
            all_updates.sort(key=lambda x: (x['part_number'], x['operation_number'], x['start_time']))

            # Deduplicate updates, keeping the latest version
            final_updates = []
            seen_operations = {}
            for update in all_updates:
                op_key = (update['part_number'], update['operation_number'], update['start_time'])
                if op_key not in seen_operations or update['new_version'] > seen_operations[op_key]['new_version']:
                    seen_operations[op_key] = update
            final_updates = list(seen_operations.values())
            final_updates.sort(key=lambda x: (x['part_number'], x['operation_number'], x['start_time']))

            # Ensure only schedulable work centers are included
            schedulable_updates = []
            for update in final_updates:
                machine_id = update['machine_id']
                if machine_id in machine_schedulability and machine_schedulability[machine_id]:
                    schedulable_updates.append(update)
                else:
                    print(f"Removing update for machine {machine_id} as it's in a non-schedulable work center")

            # OPTIMIZED: Process logs with bulk operations and reduce redundant queries
            combined_logs = {}
            production_logs = []
            total_completed = 0
            total_rejected = 0

            # Process logs and create combined logs - OPTIMIZED using pre-fetched data
            for log in all_production_logs:
                try:
                    if log.end_time is None:
                        continue

                    operator = log.operator
                    if log.schedule_version:
                        schedule_item = log.schedule_version.schedule_item
                        machine = schedule_item.machine
                        operation = schedule_item.operation
                        order = schedule_item.order
                        version_number = log.schedule_version.version_number
                    else:
                        operation = log.operation
                        machine = operation.machine if operation else None
                        order = operation.order if operation else None
                        version_number = None
                        schedule_item = None

                    machine_name = None
                    if machine:
                        if hasattr(machine, 'work_center') and machine.work_center:
                            machine_name = f"{machine.work_center.code}-{machine.make}"
                        else:
                            machine_name = machine.make

                    # Group logs by part_number, operation, and machine_name
                    group_key = (
                        order.part_number if order else None,
                        operation.operation_description if operation else None,
                        machine_name
                    )

                    if group_key not in combined_logs:
                        combined_logs[group_key] = {
                            'logs': [],
                            'start_time': None,
                            'end_time': None,
                            'quantity_completed': 0,
                            'quantity_rejected': 0,
                            'operator_id': operator.id if operator else None,
                            'part_number': order.part_number if order else None,
                            'production_order': order.production_order if order else None,
                            'operation_description': operation.operation_description if operation else None,
                            'machine_name': machine_name,
                            'notes': []
                        }

                    # Add the log to the group
                    combined_logs[group_key]['logs'].append(log)

                    # Update the aggregate data
                    if log.start_time and (combined_logs[group_key]['start_time'] is None or
                                           log.start_time < combined_logs[group_key]['start_time']):
                        combined_logs[group_key]['start_time'] = log.start_time

                    if log.end_time and (combined_logs[group_key]['end_time'] is None or
                                         log.end_time > combined_logs[group_key]['end_time']):
                        combined_logs[group_key]['end_time'] = log.end_time

                    if log.quantity_completed:
                        combined_logs[group_key]['quantity_completed'] += log.quantity_completed

                    if log.quantity_rejected:
                        combined_logs[group_key]['quantity_rejected'] += log.quantity_rejected

                    if log.notes:
                        combined_logs[group_key]['notes'].append(log.notes)

                except Exception as e:
                    print(f"Error processing log ID {log.id}: {e}")
                    continue

            # Create the production log response from the combined data
            for group_key, group_data in combined_logs.items():
                if group_data['logs']:
                    # Use the ID from the last log in the group (most recent)
                    log_id = group_data['logs'][-1].id

                    # Join all notes with a separator
                    notes = " | ".join(group_data['notes']) if group_data['notes'] else ""

                    # Create the log entry with production_order field
                    log_entry = ProductionLogResponse(
                        id=log_id,
                        operator_id=group_data['operator_id'],
                        start_time=group_data['start_time'],
                        end_time=group_data['end_time'],
                        quantity_completed=group_data['quantity_completed'],
                        quantity_rejected=group_data['quantity_rejected'],
                        part_number=group_data['part_number'],
                        production_order=group_data['production_order'],
                        operation_description=group_data['operation_description'],
                        machine_name=group_data['machine_name'],
                        notes=notes,
                        version_number=None
                    )

                    production_logs.append(log_entry)
                    total_completed += group_data['quantity_completed']
                    total_rejected += group_data['quantity_rejected']

            # Fetch operation data and schedule operations
            df = fetch_operations()
            component_quantities = fetch_component_quantities()
            lead_times = fetch_lead_times()

            # Create a filter function for schedulable machines
            def filter_schedulable_machines(operations_df):
                """Filter operations dataframe to only include operations for schedulable work centers"""
                if operations_df.empty:
                    return operations_df

                # Create a list of schedulable machine IDs
                schedulable_machine_ids = [
                    machine_id for machine_id, is_schedulable in machine_schedulability.items()
                    if is_schedulable
                ]

                # Filter the dataframe to only include operations for schedulable machines
                return operations_df[operations_df['machine_id'].isin(schedulable_machine_ids)]

            # Filter operations before scheduling
            filtered_df = filter_schedulable_machines(df)

            # Only schedule if there are operations for schedulable machines
            if not filtered_df.empty:
                schedule_df, overall_end_time, overall_time, daily_production, _, _ = schedule_operations(
                    filtered_df, component_quantities, lead_times
                )
            else:
                # Create empty dataframe with appropriate columns if no schedulable operations
                import pandas as pd
                schedule_df = pd.DataFrame(columns=['partno', 'operation', 'machine_id', 'start_time',
                                                    'end_time', 'quantity'])
                overall_end_time = datetime.now()
                overall_time = timedelta(0)
                daily_production = {}

            combined_schedule = {}
            scheduled_operations = []

            if not schedule_df.empty:
                # OPTIMIZED: Build lookup dictionaries once
                machine_to_work_center = {}
                machine_details = {}
                for machine in Machine.select().prefetch(Machine.work_center):
                    if hasattr(machine, 'work_center') and machine.work_center:
                        machine_to_work_center[machine.id] = {
                            'code': machine.work_center.code,
                            'name': machine.make,
                            'is_schedulable': machine.work_center.is_schedulable
                        }
                        machine_details[machine.id] = f"{machine.work_center.code}-{machine.make}"

                # OPTIMIZED: Build orders map once
                orders_map = {
                    order.part_number: order.production_order
                    for order in Order.select()
                }

                # Create a mapping for part descriptions - OPTIMIZED
                part_descriptions = {}
                for order in Order.select():
                    if hasattr(order, 'part_description') and order.part_description:
                        part_descriptions[order.part_number] = order.part_description
                    else:
                        part_descriptions[order.part_number] = order.part_number

                for _, row in schedule_df.iterrows():
                    # Skip operations in non-schedulable work centers
                    machine_id = row['machine_id']
                    if machine_id in machine_to_work_center and not machine_to_work_center[machine_id][
                        'is_schedulable']:
                        continue

                    quantity_str = row['quantity']
                    total_qty = 1
                    current_qty = 1
                    today_qty = 1

                    if "Process" in quantity_str:
                        match = re.search(r'Process\((\d+)/(\d+)pcs, Today: (\d+)pcs\)', quantity_str)
                        if match:
                            current_qty = int(match.group(1))
                            total_qty = int(match.group(2))
                            today_qty = int(match.group(3))
                        else:
                            match = re.search(r'Process\((\d+)/(\d+)pcs\)', quantity_str)
                            if match:
                                current_qty = int(match.group(1))
                                total_qty = int(match.group(2))
                                today_qty = current_qty

                    schedule_key = (
                        row['partno'],
                        row['operation'],
                        machine_details.get(row['machine_id'], f"Machine-{row['machine_id']}"),
                        orders_map.get(row['partno'], '')
                    )

                    is_setup = total_qty == 1

                    if is_setup:
                        if schedule_key not in combined_schedule:
                            combined_schedule[schedule_key] = {
                                'setup_start': row['start_time'],
                                'setup_end': row['end_time'],
                                'operation_end': None,
                                'total_qty': 0,
                                'current_qty': 0,
                                'today_qty': 0
                            }
                    else:
                        if schedule_key in combined_schedule:
                            combined_schedule[schedule_key]['operation_end'] = row['end_time']
                            combined_schedule[schedule_key]['total_qty'] = max(
                                combined_schedule[schedule_key]['total_qty'], total_qty)
                            combined_schedule[schedule_key]['current_qty'] = max(
                                combined_schedule[schedule_key]['current_qty'], current_qty)
                            combined_schedule[schedule_key]['today_qty'] = max(
                                combined_schedule[schedule_key]['today_qty'], today_qty)
                        else:
                            combined_schedule[schedule_key] = {
                                'setup_start': row['start_time'],
                                'setup_end': row['end_time'],
                                'operation_end': row['end_time'],
                                'total_qty': total_qty,
                                'current_qty': current_qty,
                                'today_qty': today_qty
                            }

            for (component, description, machine, production_order), data in combined_schedule.items():
                end_time = data['operation_end'] if data['operation_end'] else data['setup_end']
                if end_time and data['setup_start']:
                    quantity_str = f"Process({data['current_qty']}/{data['total_qty']}pcs, Today: {data['today_qty']}pcs)"
                    part_description = part_descriptions.get(component, component)

                    scheduled_operations.append(
                        ScheduledOperation(
                            component=component,
                            part_description=part_description,
                            description=description,
                            machine=machine,
                            start_time=data['setup_start'],
                            end_time=end_time,
                            quantity=quantity_str,
                            production_order=production_order
                        )
                    )

            # OPTIMIZED: Use pre-fetched work center data
            work_center_data = []
            for work_center in work_centers:
                machines_in_wc = []
                for machine in work_center.machines:
                    machines_in_wc.append({
                        "id": str(machine.id),
                        "name": machine.make,
                        "model": machine.model,
                        "type": machine.type
                    })
                work_center_data.append(
                    WorkCenterInfo(
                        work_center_code=work_center.code,
                        work_center_name=work_center.work_center_name or "",
                        machines=machines_in_wc,
                        is_schedulable=work_center.is_schedulable
                    )
                )

            for update in final_updates:
                if 'operation_id' not in update or update['operation_id'] is None or not isinstance(
                        update['operation_id'], int):
                    if 'item_id' in update and update['item_id'] is not None:
                        update['operation_id'] = update['item_id']
                    else:
                        hash_input = f"{update.get('part_number', '')}:{update.get('operation_number', '')}"
                        hash_value = int(hashlib.md5(hash_input.encode()).hexdigest(), 16)
                        update['operation_id'] = hash_value % 1000000

            return CombinedScheduleResponse(
                reschedule=schedulable_updates,
                total_updates=len(schedulable_updates),
                production_logs=production_logs,
                scheduled_operations=scheduled_operations,
                overall_end_time=overall_end_time,
                overall_time=str(overall_time),
                daily_production=daily_production,
                total_completed=total_completed,
                total_rejected=total_rejected,
                total_logs=len(production_logs),
                work_centers=work_center_data
            )

    except Exception as e:
        print(f"Error in dynamic rescheduling: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error during rescheduling: {str(e)}"
        )


@router.get("/reschedule-actual-planned-combined", response_model=CombinedScheduleResponse)
async def get_combined_schedule():
    """
    Combines dynamic rescheduling with actual vs planned production data
    Uses actual production logs to reschedule subsequent operations
    Preserves original planned schedule
    Accounts for partial quantity completion
    """
    return await dynamic_reschedule()