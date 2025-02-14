from fastapi import APIRouter, HTTPException
from pony.orm import db_session, select, desc
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from app.algorithm.scheduling import adjust_to_shift_hours
from app.models import PlannedScheduleItem, ScheduleVersion, ProductionLog, Order, Operation, InventoryStatus, Status, \
    MachineStatus, Machine

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
            # Get all scheduled items ordered by priority
            schedule_items = select(p for p in PlannedScheduleItem
                                    if p.status == 'scheduled'
                                    ).order_by(lambda p: (p.order.project.priority, p.initial_start_time))[:]

            machine_end_times = {}
            updates = []

            for item in schedule_items:
                try:
                    # Get current active version
                    current_version = select(v for v in ScheduleVersion
                                             if v.schedule_item == item and
                                             v.is_active == True).first()

                    if not current_version:
                        continue

                    # Check raw material availability
                    raw_available, raw_time = check_raw_material_status(
                        item.order, datetime.utcnow()
                    )

                    if not raw_available:
                        print(f"Raw material not available for item {item.id}")
                        continue

                    # Get all dependent operations for this part
                    dependent_ops = select(o for o in Operation
                                           if o.order == item.order
                                           ).order_by(lambda o: o.operation_number)[:]

                    # Convert to list of dicts for find_last_available_operation
                    operations = list(dependent_ops)

                    # Get production logs
                    logs = select(l for l in ProductionLog
                                  if l.schedule_version == current_version)[:]

                    # Get machine's last end time
                    machine_id = item.machine.id
                    last_end_time = machine_end_times.get(machine_id, current_version.planned_start_time)

                    # Determine start time considering both raw material and last end time
                    start_time = max(
                        last_end_time,
                        raw_time if raw_time else datetime.min
                    )
                    start_time = adjust_to_shift_hours(start_time)

                    # Find last available operation considering machine status
                    last_available_idx = find_last_available_operation(operations, start_time)

                    # If this operation cannot be scheduled due to machine status, skip it
                    current_op_idx = next((i for i, op in enumerate(operations)
                                           if op.id == item.operation.id), -1)

                    if current_op_idx > last_available_idx:
                        print(f"Operation {item.operation.id} cannot be scheduled due to machine status")
                        continue

                    # Calculate completed quantity from logs
                    completed_qty = sum(log.quantity_completed for log in logs)
                    remaining_qty = max(0, item.total_quantity - completed_qty)

                    # Calculate processing times
                    setup_time = float(item.operation.setup_time) * 60
                    cycle_time = float(item.operation.ideal_cycle_time) * 60
                    total_time = setup_time if not logs else 0  # Skip setup if logs exist
                    total_time += cycle_time * remaining_qty

                    end_time = start_time + timedelta(minutes=total_time)

                    # Adjust end time to shift hours if needed
                    current_time = start_time
                    actual_end_time = start_time

                    while total_time > 0:
                        shift_end = current_time.replace(hour=17, minute=0, second=0, microsecond=0)

                        if current_time + timedelta(minutes=total_time) <= shift_end:
                            actual_end_time = current_time + timedelta(minutes=total_time)
                            break

                        minutes_today = (shift_end - current_time).total_seconds() / 60
                        total_time -= minutes_today
                        current_time = (shift_end + timedelta(days=1)).replace(
                            hour=9, minute=0, second=0, microsecond=0
                        )
                        actual_end_time = shift_end

                    if actual_end_time > start_time:
                        # Update machine end time
                        machine_end_times[machine_id] = actual_end_time

                        # Create new version
                        new_version_number = current_version.version_number + 1

                        new_version = ScheduleVersion(
                            schedule_item=item,
                            version_number=new_version_number,
                            planned_start_time=start_time,
                            planned_end_time=actual_end_time,
                            planned_quantity=item.total_quantity,
                            completed_quantity=completed_qty,
                            remaining_quantity=remaining_qty,
                            is_active=True,
                            created_at=datetime.utcnow()
                        )

                        # Deactivate current version
                        current_version.is_active = False

                        # Update planned schedule item
                        item.current_version = new_version_number
                        item.remaining_quantity = remaining_qty

                        updates.append({
                            'item_id': item.id,
                            'old_version': current_version.version_number,
                            'new_version': new_version_number,
                            'completed_qty': completed_qty,
                            'remaining_qty': remaining_qty,
                            'start_time': start_time.isoformat(),
                            'end_time': actual_end_time.isoformat(),
                            'machine_id': machine_id,
                            'raw_material_status': 'Available',
                            'operation_number': item.operation.operation_number,
                            'last_available_operation': last_available_idx
                        })

                except Exception as item_error:
                    print(f"Error processing item {item.id}: {str(item_error)}")
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