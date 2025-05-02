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

router = APIRouter(prefix="/api/v1/rescheduling", tags=["rescheduling"])


def adjust_to_shift_hours(time: datetime) -> datetime:
    """Adjust time to fit within shift hours (9 AM to 5 PM)"""
    if time.hour < 9:
        return time.replace(hour=9, minute=0, second=0, microsecond=0)
    elif time.hour >= 17:
        return (time + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    return time


def check_machine_status(machine_id: int, time: datetime) -> Tuple[bool, datetime]:
    """Check if a machine is available at a given time using algorithm logic"""
    with db_session:
        machine_status = select((ms, s) for ms in MachineStatus
                                for s in Status
                                if ms.machine.id == machine_id and
                                ms.status == s).first()

        print(f"\nChecking Machine ID: {machine_id}")
        if not machine_status:
            print(f"No status found for Machine ID {machine_id}. Assuming unavailable.")
            return False, None

        ms, status = machine_status
        print(f"Machine Status Details: {status.name}")

        if status.name.upper() == 'OFF':
            print(f"Machine {machine_id} is OFF")
            return False, None

        if ms.available_from and time < ms.available_from:
            print(f"Machine {machine_id} not available before {ms.available_from}")
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


# New utility function to calculate operation delay
def calculate_operation_delay(actual_end_time: datetime, planned_end_time: datetime) -> Optional[timedelta]:
    """
    Calculate the delay between actual and planned end times

    Args:
        actual_end_time: The actual time the operation ended
        planned_end_time: The planned end time for the operation

    Returns:
        The delay as a timedelta, or None if there's no delay
    """
    if actual_end_time > planned_end_time:
        return actual_end_time - planned_end_time
    return None


def propagate_delay_to_dependent_operations(part_number: str, completed_operation_number: int,
                                            actual_end_time: datetime):
    """
    Propagate scheduling changes to operations that depend on the completed operation

    Args:
        part_number: The part number of the operations
        completed_operation_number: The operation number that was completed
        actual_end_time: The actual time the operation ended

    Returns:
        List of updated schedule items
    """
    updated_items = []

    with db_session:
        # Get the most recent order with this part number instead of assuming there's only one
        orders = select(o for o in Order if o.part_number == part_number).order_by(lambda o: desc(o.id))
        order = orders.first()

        if not order:
            print(f"Order not found for part number: {part_number}")
            return updated_items

        # Get all operations for this part number ordered by operation number
        operations = list(
            select(op for op in Operation if op.order == order).order_by(lambda op: op.operation_number)[:])

        # Find the index of the completed operation
        completed_op_index = -1
        for i, op in enumerate(operations):
            if op.operation_number == completed_operation_number:
                completed_op_index = i
                break

        if completed_op_index == -1 or completed_op_index == len(operations) - 1:
            # Either the operation wasn't found or it's the last operation (no dependents)
            print(f"Operation {completed_operation_number} not found or is the last operation in the sequence")
            return updated_items

        print(f"Found operation at index {completed_op_index} out of {len(operations)} operations")

        # The key logic: start each dependent operation immediately after the previous one finishes
        current_start_time = actual_end_time

        # Process all operations that come after the completed operation
        for i in range(completed_op_index + 1, len(operations)):
            dependent_op = operations[i]
            print(f"Processing dependent operation {dependent_op.operation_number}")

            # Find all scheduled items for this operation
            schedule_items = list(select(item for item in PlannedScheduleItem
                                         if item.operation == dependent_op)[:])

            if not schedule_items:
                print(f"No scheduled items found for operation {dependent_op.operation_number}")
                continue

            # Sort by ID to get the latest item
            schedule_items.sort(key=lambda x: x.id, reverse=True)
            latest_item = schedule_items[0]

            # Get the current active version
            current_version = select(v for v in ScheduleVersion
                                     if v.schedule_item == latest_item and
                                     v.is_active == True).first()

            if not current_version:
                print(f"No active version found for operation {dependent_op.operation_number}")
                continue

            # Calculate operation duration from current schedule
            operation_duration = current_version.planned_end_time - current_version.planned_start_time

            # New start time is the end time of the previous operation
            new_start_time = adjust_to_shift_hours(current_start_time)

            # Calculate new end time based on the original duration
            new_end_time = new_start_time + operation_duration
            new_end_time = adjust_to_shift_hours(new_end_time)

            print(f"Rescheduling operation {dependent_op.operation_number}:")
            print(f"  - Original: {current_version.planned_start_time} to {current_version.planned_end_time}")
            print(f"  - New: {new_start_time} to {new_end_time}")

            # Create a new version with the updated times
            new_version_number = current_version.version_number + 1
            new_version = ScheduleVersion(
                schedule_item=latest_item,
                version_number=new_version_number,
                planned_start_time=new_start_time,
                planned_end_time=new_end_time,
                planned_quantity=current_version.planned_quantity,
                completed_quantity=current_version.completed_quantity,
                remaining_quantity=current_version.remaining_quantity,
                is_active=True,
                created_at=datetime.utcnow()
            )

            # Deactivate current version
            current_version.is_active = False

            # Update the item's current version
            latest_item.current_version = new_version_number

            # Find last available operation for this update
            dependent_ops = select(o for o in Operation
                                   if o.order == latest_item.order
                                   ).order_by(lambda o: o.operation_number)[:]
            last_available_idx = find_last_available_operation(list(dependent_ops), new_start_time)

            # Add to updated items
            updated_items.append({
                'item_id': latest_item.id,
                'old_version': current_version.version_number,
                'new_version': new_version_number,
                'completed_qty': current_version.completed_quantity,
                'remaining_qty': current_version.remaining_quantity,
                'start_time': new_start_time.isoformat(),
                'end_time': new_end_time.isoformat(),
                'machine_id': latest_item.machine.id,
                'raw_material_status': 'Available',
                'operation_number': dependent_op.operation_number,
                'last_available_operation': last_available_idx,  # Added this required field
                'part_number': part_number,
                'production_order': order.production_order
            })

            # Update the start time for the next operation to be the end of this one
            current_start_time = new_end_time

    return updated_items



# Also update the dynamic_reschedule function to ensure all required fields are included:

@router.post("/dynamic-reschedule")
async def dynamic_reschedule():
    """Dynamically reschedule operations based on production logs with improved cascade effect"""
    try:
        with db_session:
            # Get all items ordered by operation number and ID
            schedule_items = select(p for p in PlannedScheduleItem
                                    ).order_by(lambda p: (p.operation.operation_number, p.id))[:]

            if not schedule_items:
                # Return an empty response with required fields when no items are found
                empty_work_centers = []

                # Work centers are required, so get them even if no schedule items
                for work_center in WorkCenter.select():
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
                            machines=machines_in_wc
                        )
                    )

                return CombinedScheduleResponse(
                    reschedule=[],
                    total_updates=0,
                    production_logs=[],
                    scheduled_operations=[],
                    overall_end_time=datetime.now(),  # Use current time as fallback
                    overall_time="0",
                    daily_production={},
                    total_completed=0,
                    total_rejected=0,
                    total_logs=0,
                    work_centers=empty_work_centers
                )

            updates = []
            cascade_updates = []
            valid_part_numbers = set()  # To track part numbers with valid production logs
            grouped_items = {}  # Dictionary to hold groups by machine and operation number

            # First pass: Identify part numbers with production logs
            for item in schedule_items:
                # Get all versions for this item
                versions = select(v for v in ScheduleVersion
                                  if v.schedule_item == item)[:]

                # Check if there are any production logs for any version of this item
                has_logs = False
                for version in versions:
                    logs = select(l for l in ProductionLog
                                  if l.schedule_version == version)[:]
                    if logs:
                        has_logs = True
                        valid_part_numbers.add(item.order.part_number)
                        break

                # If no logs found through versions, check for logs with direct operation reference
                if not has_logs:
                    logs = select(l for l in ProductionLog
                                  if l.schedule_version is None and l.operation == item.operation)[:]
                    if logs:
                        valid_part_numbers.add(item.order.part_number)

            print(f"Valid part numbers with production logs: {valid_part_numbers}")

            # Group items by part number, machine and operation number
            for item in schedule_items:
                # Only include items with valid part numbers
                if item.order.part_number in valid_part_numbers:
                    key = (item.machine.id, item.operation.operation_number, item.order.part_number)
                    if key not in grouped_items:
                        grouped_items[key] = []
                    grouped_items[key].append(item)

            # Dictionary to track latest end times per part number
            completed_operations = {}  # {part_number: [(operation_number, end_time)]}

            # Process each group
            for (machine_id, operation_number, part_number), items in grouped_items.items():
                try:
                    if not items:
                        continue

                    # Sort items by ID to ensure consistent ordering
                    items.sort(key=lambda x: x.id)
                    last_item = items[-1]  # Get the last item in the group

                    # Get the current version for the last item
                    current_version = select(v for v in ScheduleVersion
                                             if v.schedule_item == last_item and
                                             v.is_active == True).first()

                    if not current_version:
                        continue

                    # Get all logs for the items in this group
                    all_group_logs = []

                    for item in items:
                        # Get logs using schedule_version
                        item_logs_with_version = select(l for l in ProductionLog
                                                        for v in ScheduleVersion
                                                        if v.schedule_item == item and
                                                        l.schedule_version == v
                                                        ).order_by(lambda l: l.start_time)[:]

                        # Get logs using operation if schedule_version is None
                        item_logs_without_version = select(l for l in ProductionLog
                                                           if l.schedule_version is None and
                                                           l.operation == item.operation
                                                           ).order_by(lambda l: l.start_time)[:]

                        all_group_logs.extend(item_logs_with_version)
                        all_group_logs.extend(item_logs_without_version)

                    # Skip if no production logs exist for this group
                    if not all_group_logs:
                        continue

                    # Calculate times from actual production logs
                    valid_start_times = [log.start_time for log in all_group_logs if log.start_time is not None]
                    valid_end_times = [log.end_time for log in all_group_logs if log.end_time is not None]

                    if not valid_start_times or not valid_end_times:
                        continue

                    group_start_time = min(valid_start_times)
                    group_end_time = max(valid_end_times)

                    # Store operation completion info for later cascade processing
                    if part_number not in completed_operations:
                        completed_operations[part_number] = []

                    completed_operations[part_number].append((operation_number, group_end_time))

                    # Calculate completed quantity for the last item
                    # We need to handle both log types: with version and without version
                    last_item_logs = []
                    for log in all_group_logs:
                        if hasattr(log,
                                   'schedule_version') and log.schedule_version and log.schedule_version.schedule_item == last_item:
                            last_item_logs.append(log)
                        elif log.operation == last_item.operation:
                            last_item_logs.append(log)

                    completed_qty = sum(
                        log.quantity_completed for log in last_item_logs if log.quantity_completed is not None)
                    remaining_qty = max(0, last_item.total_quantity - completed_qty)

                    # Get last available operation
                    dependent_ops = select(o for o in Operation
                                           if o.order == last_item.order
                                           ).order_by(lambda o: o.operation_number)[:]
                    last_available_idx = find_last_available_operation(list(dependent_ops), group_start_time)

                    # Create new version for the last item
                    new_version_number = current_version.version_number + 1
                    new_version = ScheduleVersion(
                        schedule_item=last_item,
                        version_number=new_version_number,
                        planned_start_time=group_start_time,
                        planned_end_time=group_end_time,
                        planned_quantity=last_item.total_quantity,
                        completed_quantity=completed_qty,
                        remaining_quantity=remaining_qty,
                        is_active=True,
                        created_at=datetime.utcnow()
                    )

                    # Deactivate current version
                    current_version.is_active = False

                    # Update planned schedule item
                    last_item.current_version = new_version_number
                    last_item.remaining_quantity = remaining_qty
                    last_item.status = 'scheduled'

                    # Check and get operation_id safely
                    operation_id = last_item.operation.id if last_item.operation else last_item.id

                    # Add update with guaranteed operation_id
                    updates.append({
                        'item_id': last_item.id,
                        'operation_id': operation_id,  # This is now guaranteed to have a value
                        'old_version': current_version.version_number,
                        'new_version': new_version_number,
                        'completed_qty': completed_qty,
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

                except Exception as group_error:
                    print(
                        f"Error processing group for machine {machine_id}, operation {operation_number}: {str(group_error)}")
                    continue

            # Now process cascade updates by part number
            for part_number, operations in completed_operations.items():
                # Sort operations by operation number to ensure we process in the right order
                operations.sort(key=lambda x: x[0])

                for op_num, end_time in operations:
                    # Only process operations that have been completed
                    if end_time:
                        # Propagate the end time to all dependent operations
                        cascade_results = propagate_delay_to_dependent_operations(
                            part_number, op_num, end_time)

                        if cascade_results:
                            print(
                                f"Propagated changes for part {part_number}, operation {op_num}: {len(cascade_results)} operations affected")

                            # Ensure operation_id is present in cascade_results
                            for result in cascade_results:
                                if 'operation_id' not in result or result['operation_id'] is None:
                                    # Try to find the operation in the database to get its ID
                                    try:
                                        operation = select(o for o in Operation
                                                           if o.order.part_number == result['part_number'] and
                                                           o.operation_number == result['operation_number']).first()

                                        if operation:
                                            result['operation_id'] = operation.id
                                        else:
                                            # Use item_id as fallback if available
                                            if 'item_id' in result and result['item_id'] is not None:
                                                result['operation_id'] = result['item_id']
                                            else:
                                                # Generate a deterministic integer ID based on part_number and operation_number
                                                import hashlib
                                                hash_input = f"{result['part_number']}:{result['operation_number']}"
                                                hash_value = int(hashlib.md5(hash_input.encode()).hexdigest(), 16)
                                                result['operation_id'] = hash_value % 1000000
                                    except Exception as e:
                                        print(f"Error finding operation ID for cascade update: {e}")
                                        # Fallback to a deterministic hash
                                        result['operation_id'] = abs(hash(str(result))) % 1000000

                            cascade_updates.extend(cascade_results)

            # Combine all updates
            all_updates = updates + cascade_updates

            # Sort updates by operation_number to ensure proper sequencing
            all_updates.sort(key=lambda x: (x['part_number'], x['operation_number']))

            # Get production logs with related information
            logs_query = []
            for log in ProductionLog.select():
                try:
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

                    logs_query.append((
                        log,
                        operator,
                        log.schedule_version,
                        schedule_item,
                        machine,
                        operation,
                        order,
                        version_number
                    ))
                except Exception as e:
                    print(f"Error processing log ID {log.id}: {e}")
                    continue

            # Dictionary to store combined logs
            combined_logs = {}
            production_logs = []
            total_completed = 0
            total_rejected = 0

            for result in logs_query:
                log, operator, version, schedule_item, machine, operation, order, version_number = result

                if log.end_time is None:
                    continue

                # Handle cases where machine might not have work_center attribute
                machine_name = None
                if machine:
                    if hasattr(machine, 'work_center') and machine.work_center:
                        machine_name = f"{machine.work_center.code}-{machine.make}"
                    else:
                        machine_name = machine.make

                group_key = (
                    order.part_number if order else None,
                    operation.operation_description if operation else None,
                    machine_name,
                    version_number
                )

                is_setup = log.quantity_completed == 1

                if group_key not in combined_logs:
                    combined_logs[group_key] = {
                        'setup': None,
                        'operation': None
                    }

                if is_setup:
                    combined_logs[group_key]['setup'] = {
                        'id': log.id,
                        'start_time': log.start_time,
                        'notes': log.notes or ''
                    }
                else:
                    combined_logs[group_key]['operation'] = {
                        'id': log.id,
                        'end_time': log.end_time,
                        'quantity_completed': log.quantity_completed or 0,
                        'quantity_rejected': log.quantity_rejected or 0,
                        'operator_id': operator.id if operator else None,
                        'part_number': order.part_number if order else None,
                        'operation_description': operation.operation_description if operation else None,
                        'machine_name': machine_name,
                        'version_number': version_number,
                        'notes': log.notes or ''
                    }

            # Process combined logs
            for group_data in combined_logs.values():
                setup = group_data['setup']
                operation = group_data['operation']

                if setup and operation:
                    log_entry = ProductionLogResponse(
                        id=operation['id'],
                        operator_id=operation['operator_id'],
                        start_time=setup['start_time'],
                        end_time=operation['end_time'],
                        quantity_completed=operation['quantity_completed'],
                        quantity_rejected=operation['quantity_rejected'],
                        part_number=operation['part_number'],
                        operation_description=operation['operation_description'],
                        machine_name=operation['machine_name'],
                        notes=f"Setup: {setup['notes']} | Operation: {operation['notes']}",
                        version_number=operation['version_number']
                    )
                    production_logs.append(log_entry)
                    total_completed += operation['quantity_completed']
                    total_rejected += operation['quantity_rejected']

            # Get schedule data
            df = fetch_operations()
            component_quantities = fetch_component_quantities()
            lead_times = fetch_lead_times()

            schedule_df, overall_end_time, overall_time, daily_production, _, _ = schedule_operations(
                df, component_quantities, lead_times
            )

            # Dictionary to store combined schedule operations
            combined_schedule = {}
            scheduled_operations = []

            if not schedule_df.empty:
                machine_details = {
                    machine.id: f"{machine.work_center.code}-{machine.make}"
                    for machine in Machine.select()
                    if hasattr(machine, 'work_center') and machine.work_center
                }

                orders_map = {
                    order.part_number: order.production_order
                    for order in Order.select()
                }

                for _, row in schedule_df.iterrows():
                    # Include all scheduled operations without filtering by valid_part_numbers
                    # Extract quantities from the quantity string
                    quantity_str = row['quantity']
                    total_qty = 1
                    current_qty = 1
                    today_qty = 1

                    if "Process" in quantity_str:
                        import re
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

                    # Create key for grouping schedule operations
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

            # Process combined schedule into scheduled operations
            for (component, description, machine, production_order), data in combined_schedule.items():
                # Use setup_end as fallback when operation_end is not available
                end_time = data['operation_end'] if data['operation_end'] else data['setup_end']

                if end_time and data['setup_start']:  # Only include operations with valid start and end times
                    quantity_str = f"Process({data['current_qty']}/{data['total_qty']}pcs, Today: {data['today_qty']}pcs)"
                    scheduled_operations.append(
                        ScheduledOperation(
                            component=component,
                            description=description,
                            machine=machine,
                            start_time=data['setup_start'],
                            end_time=end_time,
                            quantity=quantity_str,
                            production_order=production_order
                        )
                    )

            # Query work centers and their machines
            work_center_data = []
            for work_center in WorkCenter.select():
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
                        machines=machines_in_wc
                    )
                )

            # Final validation of operation_id in all updates
            for update in all_updates:
                if 'operation_id' not in update or update['operation_id'] is None or not isinstance(
                        update['operation_id'], int):
                    # Use item_id as fallback if available
                    if 'item_id' in update and update['item_id'] is not None:
                        update['operation_id'] = update['item_id']
                    else:
                        # Create a deterministic integer from available data
                        import hashlib
                        hash_input = f"{update.get('part_number', '')}:{update.get('operation_number', '')}"
                        hash_value = int(hashlib.md5(hash_input.encode()).hexdigest(), 16)
                        update['operation_id'] = hash_value % 1000000

            # Return the complete response with all required fields
            return CombinedScheduleResponse(
                reschedule=all_updates,
                total_updates=len(all_updates),
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
    try:
        with db_session:
            # Get all items ordered by operation number and ID
            schedule_items = select(p for p in PlannedScheduleItem
                                    ).order_by(lambda p: (p.operation.operation_number, p.id))[:]

            if not schedule_items:
                # Return an empty response with required fields when no items are found
                empty_work_centers = []

                # Work centers are required, so get them even if no schedule items
                for work_center in WorkCenter.select():
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
                            machines=machines_in_wc
                        )
                    )

                return CombinedScheduleResponse(
                    reschedule=[],
                    total_updates=0,
                    production_logs=[],
                    scheduled_operations=[],
                    overall_end_time=datetime.now(),  # Use current time as fallback
                    overall_time="0",
                    daily_production={},
                    total_completed=0,
                    total_rejected=0,
                    total_logs=0,
                    work_centers=empty_work_centers
                )

            updates = []
            cascade_updates = []
            valid_part_numbers = set()  # To track part numbers with valid production logs
            grouped_items = {}  # Dictionary to hold groups by machine and operation number

            # First pass: Identify part numbers with production logs
            for item in schedule_items:
                # Get all versions for this item
                versions = select(v for v in ScheduleVersion
                                  if v.schedule_item == item)[:]

                # Check if there are any production logs for any version of this item
                has_logs = False
                for version in versions:
                    logs = select(l for l in ProductionLog
                                  if l.schedule_version == version)[:]
                    if logs:
                        has_logs = True
                        valid_part_numbers.add(item.order.part_number)
                        break

                # If no logs found through versions, check for logs with direct operation reference
                if not has_logs:
                    logs = select(l for l in ProductionLog
                                  if l.schedule_version is None and l.operation == item.operation)[:]
                    if logs:
                        valid_part_numbers.add(item.order.part_number)

            print(f"Valid part numbers with production logs: {valid_part_numbers}")

            # Group items by part number, machine and operation number
            for item in schedule_items:
                # Only include items with valid part numbers
                if item.order.part_number in valid_part_numbers:
                    key = (item.machine.id, item.operation.operation_number, item.order.part_number)
                    if key not in grouped_items:
                        grouped_items[key] = []
                    grouped_items[key].append(item)

            # Dictionary to track latest end times per part number
            completed_operations = {}  # {part_number: [(operation_number, end_time)]}

            # Process each group
            for (machine_id, operation_number, part_number), items in grouped_items.items():
                try:
                    if not items:
                        continue

                    # Sort items by ID to ensure consistent ordering
                    items.sort(key=lambda x: x.id)
                    last_item = items[-1]  # Get the last item in the group

                    # Get the current version for the last item
                    current_version = select(v for v in ScheduleVersion
                                             if v.schedule_item == last_item and
                                             v.is_active == True).first()

                    if not current_version:
                        continue

                    # Get all logs for the items in this group
                    all_group_logs = []

                    for item in items:
                        # Get logs using schedule_version
                        item_logs_with_version = select(l for l in ProductionLog
                                                        for v in ScheduleVersion
                                                        if v.schedule_item == item and
                                                        l.schedule_version == v
                                                        ).order_by(lambda l: l.start_time)[:]

                        # Get logs using operation if schedule_version is None
                        item_logs_without_version = select(l for l in ProductionLog
                                                           if l.schedule_version is None and
                                                           l.operation == item.operation
                                                           ).order_by(lambda l: l.start_time)[:]

                        all_group_logs.extend(item_logs_with_version)
                        all_group_logs.extend(item_logs_without_version)

                    # Skip if no production logs exist for this group
                    if not all_group_logs:
                        continue

                    # Calculate times from actual production logs
                    valid_start_times = [log.start_time for log in all_group_logs if log.start_time is not None]
                    valid_end_times = [log.end_time for log in all_group_logs if log.end_time is not None]

                    if not valid_start_times or not valid_end_times:
                        continue

                    group_start_time = min(valid_start_times)
                    group_end_time = max(valid_end_times)

                    # Store operation completion info for later cascade processing
                    if part_number not in completed_operations:
                        completed_operations[part_number] = []

                    completed_operations[part_number].append((operation_number, group_end_time))

                    # Calculate completed quantity for the last item
                    # We need to handle both log types: with version and without version
                    last_item_logs = []
                    for log in all_group_logs:
                        if hasattr(log,
                                   'schedule_version') and log.schedule_version and log.schedule_version.schedule_item == last_item:
                            last_item_logs.append(log)
                        elif log.operation == last_item.operation:
                            last_item_logs.append(log)

                    completed_qty = sum(
                        log.quantity_completed for log in last_item_logs if log.quantity_completed is not None)
                    remaining_qty = max(0, last_item.total_quantity - completed_qty)

                    # Get last available operation
                    dependent_ops = select(o for o in Operation
                                           if o.order == last_item.order
                                           ).order_by(lambda o: o.operation_number)[:]
                    last_available_idx = find_last_available_operation(list(dependent_ops), group_start_time)

                    # Create new version for the last item
                    new_version_number = current_version.version_number + 1
                    new_version = ScheduleVersion(
                        schedule_item=last_item,
                        version_number=new_version_number,
                        planned_start_time=group_start_time,
                        planned_end_time=group_end_time,
                        planned_quantity=last_item.total_quantity,
                        completed_quantity=completed_qty,
                        remaining_quantity=remaining_qty,
                        is_active=True,
                        created_at=datetime.utcnow()
                    )

                    # Deactivate current version
                    current_version.is_active = False

                    # Update planned schedule item
                    last_item.current_version = new_version_number
                    last_item.remaining_quantity = remaining_qty
                    last_item.status = 'scheduled'

                    # Check and get operation_id safely
                    operation_id = last_item.operation.id if last_item.operation else last_item.id

                    # Add update with guaranteed operation_id
                    updates.append({
                        'item_id': last_item.id,
                        'operation_id': operation_id,  # This is now guaranteed to have a value
                        'old_version': current_version.version_number,
                        'new_version': new_version_number,
                        'completed_qty': completed_qty,
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

                except Exception as group_error:
                    print(
                        f"Error processing group for machine {machine_id}, operation {operation_number}: {str(group_error)}")
                    continue

            # Now process cascade updates by part number
            for part_number, operations in completed_operations.items():
                # Sort operations by operation number to ensure we process in the right order
                operations.sort(key=lambda x: x[0])

                for op_num, end_time in operations:
                    # Only process operations that have been completed
                    if end_time:
                        # Propagate the end time to all dependent operations
                        cascade_results = propagate_delay_to_dependent_operations(
                            part_number, op_num, end_time)

                        if cascade_results:
                            print(
                                f"Propagated changes for part {part_number}, operation {op_num}: {len(cascade_results)} operations affected")

                            # Ensure operation_id is present in cascade_results
                            for result in cascade_results:
                                if 'operation_id' not in result or result['operation_id'] is None:
                                    # Try to find the operation in the database to get its ID
                                    try:
                                        operation = select(o for o in Operation
                                                           if o.order.part_number == result['part_number'] and
                                                           o.operation_number == result['operation_number']).first()

                                        if operation:
                                            result['operation_id'] = operation.id
                                        else:
                                            # Use item_id as fallback if available
                                            if 'item_id' in result and result['item_id'] is not None:
                                                result['operation_id'] = result['item_id']
                                            else:
                                                # Generate a deterministic integer ID based on part_number and operation_number
                                                import hashlib
                                                hash_input = f"{result['part_number']}:{result['operation_number']}"
                                                hash_value = int(hashlib.md5(hash_input.encode()).hexdigest(), 16)
                                                result['operation_id'] = hash_value % 1000000
                                    except Exception as e:
                                        print(f"Error finding operation ID for cascade update: {e}")
                                        # Fallback to a deterministic hash
                                        result['operation_id'] = abs(hash(str(result))) % 1000000

                            cascade_updates.extend(cascade_results)

            # Combine all updates
            all_updates = updates + cascade_updates

            # Sort updates by operation_number to ensure proper sequencing
            all_updates.sort(key=lambda x: (x['part_number'], x['operation_number']))

            # Get production logs with related information
            logs_query = []
            for log in ProductionLog.select():
                try:
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

                    logs_query.append((
                        log,
                        operator,
                        log.schedule_version,
                        schedule_item,
                        machine,
                        operation,
                        order,
                        version_number
                    ))
                except Exception as e:
                    print(f"Error processing log ID {log.id}: {e}")
                    continue

            # Dictionary to store combined logs
            combined_logs = {}
            production_logs = []
            total_completed = 0
            total_rejected = 0

            for result in logs_query:
                log, operator, version, schedule_item, machine, operation, order, version_number = result

                if log.end_time is None:
                    continue

                # Handle cases where machine might not have work_center attribute
                machine_name = None
                if machine:
                    if hasattr(machine, 'work_center') and machine.work_center:
                        machine_name = f"{machine.work_center.code}-{machine.make}"
                    else:
                        machine_name = machine.make

                group_key = (
                    order.part_number if order else None,
                    operation.operation_description if operation else None,
                    machine_name,
                    version_number
                )

                is_setup = log.quantity_completed == 1

                if group_key not in combined_logs:
                    combined_logs[group_key] = {
                        'setup': None,
                        'operation': None
                    }

                if is_setup:
                    combined_logs[group_key]['setup'] = {
                        'id': log.id,
                        'start_time': log.start_time,
                        'notes': log.notes or ''
                    }
                else:
                    combined_logs[group_key]['operation'] = {
                        'id': log.id,
                        'end_time': log.end_time,
                        'quantity_completed': log.quantity_completed or 0,
                        'quantity_rejected': log.quantity_rejected or 0,
                        'operator_id': operator.id if operator else None,
                        'part_number': order.part_number if order else None,
                        'operation_description': operation.operation_description if operation else None,
                        'machine_name': machine_name,
                        'version_number': version_number,
                        'notes': log.notes or ''
                    }

            # Process combined logs
            for group_data in combined_logs.values():
                setup = group_data['setup']
                operation = group_data['operation']

                if setup and operation:
                    log_entry = ProductionLogResponse(
                        id=operation['id'],
                        operator_id=operation['operator_id'],
                        start_time=setup['start_time'],
                        end_time=operation['end_time'],
                        quantity_completed=operation['quantity_completed'],
                        quantity_rejected=operation['quantity_rejected'],
                        part_number=operation['part_number'],
                        operation_description=operation['operation_description'],
                        machine_name=operation['machine_name'],
                        notes=f"Setup: {setup['notes']} | Operation: {operation['notes']}",
                        version_number=operation['version_number']
                    )
                    production_logs.append(log_entry)
                    total_completed += operation['quantity_completed']
                    total_rejected += operation['quantity_rejected']

            # Get schedule data
            df = fetch_operations()
            component_quantities = fetch_component_quantities()
            lead_times = fetch_lead_times()

            schedule_df, overall_end_time, overall_time, daily_production, _, _ = schedule_operations(
                df, component_quantities, lead_times
            )

            # Dictionary to store combined schedule operations
            combined_schedule = {}
            scheduled_operations = []

            if not schedule_df.empty:
                machine_details = {
                    machine.id: f"{machine.work_center.code}-{machine.make}"
                    for machine in Machine.select()
                    if hasattr(machine, 'work_center') and machine.work_center
                }

                orders_map = {
                    order.part_number: order.production_order
                    for order in Order.select()
                }

                for _, row in schedule_df.iterrows():
                    # Include all scheduled operations without filtering by valid_part_numbers
                    # Extract quantities from the quantity string
                    quantity_str = row['quantity']
                    total_qty = 1
                    current_qty = 1
                    today_qty = 1

                    if "Process" in quantity_str:
                        import re
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

                    # Create key for grouping schedule operations
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

            # Process combined schedule into scheduled operations
            for (component, description, machine, production_order), data in combined_schedule.items():
                # Use setup_end as fallback when operation_end is not available
                end_time = data['operation_end'] if data['operation_end'] else data['setup_end']

                if end_time and data['setup_start']:  # Only include operations with valid start and end times
                    quantity_str = f"Process({data['current_qty']}/{data['total_qty']}pcs, Today: {data['today_qty']}pcs)"
                    scheduled_operations.append(
                        ScheduledOperation(
                            component=component,
                            description=description,
                            machine=machine,
                            start_time=data['setup_start'],
                            end_time=end_time,
                            quantity=quantity_str,
                            production_order=production_order
                        )
                    )

            # Query work centers and their machines
            work_center_data = []
            for work_center in WorkCenter.select():
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
                        machines=machines_in_wc
                    )
                )

            # Final validation of operation_id in all updates
            for update in all_updates:
                if 'operation_id' not in update or update['operation_id'] is None or not isinstance(
                        update['operation_id'], int):
                    # Use item_id as fallback if available
                    if 'item_id' in update and update['item_id'] is not None:
                        update['operation_id'] = update['item_id']
                    else:
                        # Create a deterministic integer from available data
                        import hashlib
                        hash_input = f"{update.get('part_number', '')}:{update.get('operation_number', '')}"
                        hash_value = int(hashlib.md5(hash_input.encode()).hexdigest(), 16)
                        update['operation_id'] = hash_value % 1000000

            # Return the complete response with all required fields
            return CombinedScheduleResponse(
                reschedule=all_updates,
                total_updates=len(all_updates),
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
        print(f"Error in combined schedule endpoint: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Error during combined scheduling: {str(e)}"
        )