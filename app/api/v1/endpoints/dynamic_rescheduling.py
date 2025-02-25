from fastapi import APIRouter, HTTPException
from pony.orm import db_session, select, desc
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from app.algorithm.scheduling import adjust_to_shift_hours, schedule_operations
from app.crud.component_quantities import fetch_component_quantities
from app.crud.leadtime import fetch_lead_times
from app.crud.operation import fetch_operations
from app.models import PlannedScheduleItem, ScheduleVersion, ProductionLog, Order, Operation, Status
from app.schemas.scheduled import CombinedScheduleResponse, WorkCenterInfo
from app.models.master_order import WorkCenter, MachineStatus, Machine
from app.schemas.scheduled import ProductionLogResponse, ScheduledOperation

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

@router.post("/dynamic-reschedule")
async def dynamic_reschedule():
    """Dynamically reschedule operations based on production logs"""
    try:
        with db_session:
            # Get all items ordered by operation number and ID
            schedule_items = select(p for p in PlannedScheduleItem
                                  ).order_by(lambda p: (p.operation.operation_number, p.id))[:]

            if not schedule_items:
                return {
                    'message': 'No schedule items found for rescheduling',
                    'updates': [],
                    'total_updates': 0
                }

            machine_end_times = {}
            updates = []
            grouped_items = {}  # Dictionary to hold groups by machine and operation number

            # Group items by machine and operation number
            for item in schedule_items:
                key = (item.machine.id, item.operation.operation_number)
                if key not in grouped_items:
                    grouped_items[key] = []
                grouped_items[key].append(item)

            # Process each group
            for (machine_id, operation_number), items in grouped_items.items():
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
                        item_logs = select(l for l in ProductionLog
                                         for v in ScheduleVersion
                                         if v.schedule_item == item and
                                         l.schedule_version == v
                                         ).order_by(lambda l: l.start_time)[:]
                        all_group_logs.extend(item_logs)

                    # Skip if no production logs exist for this group
                    if not all_group_logs:
                        continue

                    # Calculate times from actual production logs
                    group_start_time = min(log.start_time for log in all_group_logs if log.start_time is not None)
                    group_end_time = max(log.end_time for log in all_group_logs if log.end_time is not None)

                    # Calculate completed quantity for the last item
                    last_item_logs = [log for log in all_group_logs if log.schedule_version.schedule_item == last_item]
                    completed_qty = sum(log.quantity_completed for log in last_item_logs)
                    remaining_qty = max(0, last_item.total_quantity - completed_qty)

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

                    # Update machine end time
                    machine_end_times[machine_id] = group_end_time

                    # Find last available operation
                    dependent_ops = select(o for o in Operation
                                         if o.order == last_item.order
                                         ).order_by(lambda o: o.operation_number)[:]
                    last_available_idx = find_last_available_operation(list(dependent_ops), group_start_time)

                    # Add update only for the last item in the group
                    updates.append({
                        'item_id': last_item.id,
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
                        'part_number': last_item.order.part_number,
                        'production_order': last_item.order.production_order
                    })

                except Exception as group_error:
                    print(f"Error processing group for machine {machine_id}, operation {operation_number}: {str(group_error)}")
                    continue

            return {
                'message': 'Dynamic rescheduling completed',
                'updates': updates,
                'total_updates': len(updates)
            }

    except Exception as e:
        print(f"Error in dynamic rescheduling: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error during rescheduling: {str(e)}"
        )

@router.get("/reschedule-history/{item_id}")
async def get_reschedule_history(item_id: int):
    """Get version history for a scheduled item"""
    try:
        with db_session:
            schedule_item = PlannedScheduleItem.get(id=item_id)
            if not schedule_item:
                raise HTTPException(
                    status_code=404,
                    detail=f"Schedule item {item_id} not found"
                )

            versions = select(v for v in ScheduleVersion
                              if v.schedule_item.id == item_id).order_by(
                lambda v: desc(v.version_number))[:]

            history = []
            for version in versions:
                history.append({
                    'version': version.version_number,
                    'start_time': version.planned_start_time.isoformat(),
                    'end_time': version.planned_end_time.isoformat(),
                    'completed_qty': version.completed_quantity,
                    'remaining_qty': version.remaining_quantity,
                    'is_active': version.is_active,
                    'created_at': version.created_at.isoformat()
                })

            return {
                'item_id': item_id,
                'total_quantity': schedule_item.total_quantity,
                'current_version': schedule_item.current_version,
                'version_history': history,
                'total_versions': len(history)
            }

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching reschedule history: {str(e)}"
        )

@router.get("/reschedule-actual-planned-combined", response_model=CombinedScheduleResponse)
async def get_combined_schedule():
    """
    Combines dynamic rescheduling with actual vs planned production data
    """
    try:
        with db_session:
            # Get all items ordered by operation number and ID
            schedule_items = select(p for p in PlannedScheduleItem
                                  ).order_by(lambda p: (p.operation.operation_number, p.id))[:]

            machine_end_times = {}
            updates = []
            grouped_items = {}  # Dictionary to hold groups by machine and operation number

            # Group items by machine and operation number
            for item in schedule_items:
                key = (item.machine.id, item.operation.operation_number)
                if key not in grouped_items:
                    grouped_items[key] = []
                grouped_items[key].append(item)

            # Process each group (matching dynamic-reschedule logic)
            for (machine_id, operation_number), items in grouped_items.items():
                try:
                    if not items:
                        continue

                    items.sort(key=lambda x: x.id)
                    last_item = items[-1]

                    current_version = select(v for v in ScheduleVersion
                                          if v.schedule_item == last_item and
                                          v.is_active == True).first()

                    if not current_version:
                        continue

                    # Get all logs for the items in this group
                    all_group_logs = []
                    for item in items:
                        item_logs = select(l for l in ProductionLog
                                         for v in ScheduleVersion
                                         if v.schedule_item == item and
                                         l.schedule_version == v
                                         ).order_by(lambda l: l.start_time)[:]
                        all_group_logs.extend(item_logs)

                    # Skip if no production logs exist
                    if not all_group_logs:
                        continue

                    # Calculate times from actual production logs
                    group_start_time = min(log.start_time for log in all_group_logs if log.start_time is not None)
                    group_end_time = max(log.end_time for log in all_group_logs if log.end_time is not None)

                    # Calculate completed quantity for the last item
                    last_item_logs = [log for log in all_group_logs if log.schedule_version.schedule_item == last_item]
                    completed_qty = sum(log.quantity_completed for log in last_item_logs)
                    remaining_qty = max(0, last_item.total_quantity - completed_qty)

                    # Find last available operation
                    dependent_ops = select(o for o in Operation
                                         if o.order == last_item.order
                                         ).order_by(lambda o: o.operation_number)[:]
                    last_available_idx = find_last_available_operation(list(dependent_ops), group_start_time)

                    # Add update only for the last item in the group
                    updates.append({
                        'item_id': last_item.id,
                        'old_version': current_version.version_number,
                        'new_version': current_version.version_number + 1,
                        'completed_qty': completed_qty,
                        'remaining_qty': remaining_qty,
                        'start_time': group_start_time.isoformat(),
                        'end_time': group_end_time.isoformat(),
                        'machine_id': machine_id,
                        'raw_material_status': 'Available',
                        'operation_number': operation_number,
                        'last_available_operation': last_available_idx,
                        'part_number': last_item.order.part_number,
                        'production_order': last_item.order.production_order
                    })

                except Exception as group_error:
                    print(f"Error processing group for machine {machine_id}, operation {operation_number}: {str(group_error)}")
                    continue

            # Get production logs with related information
            logs_query = select((
                log,
                log.operator,
                log.schedule_version,
                log.schedule_version.schedule_item,
                log.schedule_version.schedule_item.machine,
                log.schedule_version.schedule_item.operation,
                log.schedule_version.schedule_item.order
            ) for log in ProductionLog)

            # Dictionary to store combined logs
            combined_logs = {}
            production_logs = []
            total_completed = 0
            total_rejected = 0

            for (log, operator, version, schedule_item, machine, operation, order) in logs_query:
                if log.end_time is None:
                    continue

                group_key = (
                    order.part_number if order else None,
                    operation.operation_description if operation else None,
                    machine.work_center.code + "-" + machine.make if machine and hasattr(machine, 'work_center') else None,
                    version.version_number if version else None
                )

                is_setup = log.quantity_completed == 1
                machine_name = f"{machine.work_center.code}-{machine.make}" if machine and hasattr(machine, 'work_center') else None

                if group_key not in combined_logs:
                    combined_logs[group_key] = {
                        'setup': None,
                        'operation': None
                    }

                if is_setup:
                    combined_logs[group_key]['setup'] = {
                        'id': log.id,
                        'start_time': log.start_time,
                        'notes': log.notes
                    }
                else:
                    combined_logs[group_key]['operation'] = {
                        'id': log.id,
                        'end_time': log.end_time,
                        'quantity_completed': log.quantity_completed,
                        'quantity_rejected': log.quantity_rejected,
                        'operator_id': operator.id,
                        'part_number': order.part_number if order else None,
                        'operation_description': operation.operation_description if operation else None,
                        'machine_name': machine_name,
                        'version_number': version.version_number if version else None,
                        'notes': log.notes
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
                }

                orders_map = {
                    order.part_number: order.production_order
                    for order in Order.select()
                }

                for _, row in schedule_df.iterrows():
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
                if data['operation_end']:  # Only include completed operations
                    quantity_str = f"Process({data['current_qty']}/{data['total_qty']}pcs, Today: {data['today_qty']}pcs)"
                    scheduled_operations.append(
                        ScheduledOperation(
                            component=component,
                            description=description,
                            machine=machine,
                            start_time=data['setup_start'],
                            end_time=data['operation_end'],
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

            # Return the combined response with updated reschedule data
            return CombinedScheduleResponse(
                reschedule=updates,  # Updated to match dynamic-reschedule format
                total_updates=len(updates),
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
        raise HTTPException(
            status_code=500,
            detail=f"Error during combined scheduling: {str(e)}"
        )