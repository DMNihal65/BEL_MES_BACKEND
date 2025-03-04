import json
import os

import pandas as pd
from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import List, Optional, Dict, Tuple
from datetime import datetime, timedelta
from pony.orm import db_session, select, commit

from app.schemas.operations import (
    OperationOut, ScheduledOperation, ScheduleResponse,
    MachineSchedulesOut, WorkCenterMachine
)
from app.crud.operation import fetch_operations
from app.crud.component_quantities import fetch_component_quantities
from app.crud.leadtime import fetch_lead_times
from app.algorithm.scheduling import schedule_operations
from app.models import Operation, Order, Machine, WorkCenter, MachineStatus, Status, Project
from app.models import PlannedScheduleItem, ScheduleVersion
from app.schemas.priority_schedule import PriorityUpdateResponse, PriorityUpdateRequest, RunningOperation, \
    DependentOperation

router = APIRouter(prefix="/api/v1/test", tags=["test"])

# Constants
LATEST_SCHEDULE_FILE = 'data/latest_schedule.csv'
PRIORITY_HISTORY_FILE = 'data/priority_history.json'


def ensure_directory_exists(file_path):
    """Make sure the directory for the specified file exists"""
    directory = os.path.dirname(file_path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)


def save_latest_schedule(schedule_df):
    """Save the latest schedule to a persistent file"""
    try:
        ensure_directory_exists(LATEST_SCHEDULE_FILE)
        schedule_df.to_csv(LATEST_SCHEDULE_FILE, index=False)
        return True
    except Exception as e:
        print(f"Error saving latest schedule: {e}")
        return False


def get_latest_schedule():
    """Load the latest schedule from the persistent file"""
    try:
        if os.path.exists(LATEST_SCHEDULE_FILE):
            return pd.read_csv(LATEST_SCHEDULE_FILE, parse_dates=['start_time', 'end_time'])
        return pd.DataFrame()
    except Exception as e:
        print(f"Error loading latest schedule: {e}")
        return pd.DataFrame()


def record_priority_change(part_number: str, old_priority: int, new_priority: int):
    """Record a priority change in the history file"""
    try:
        ensure_directory_exists(PRIORITY_HISTORY_FILE)

        # Load existing history or create a new one
        history = []
        if os.path.exists(PRIORITY_HISTORY_FILE):
            with open(PRIORITY_HISTORY_FILE, 'r') as f:
                try:
                    history = json.load(f)
                except json.JSONDecodeError:
                    history = []

        # Add the new record
        history.append({
            'part_number': part_number,
            'old_priority': old_priority,
            'new_priority': new_priority,
            'timestamp': datetime.now().isoformat()
        })

        # Save back to file
        with open(PRIORITY_HISTORY_FILE, 'w') as f:
            json.dump(history, f, indent=2)

        return True
    except Exception as e:
        print(f"Error recording priority change: {e}")
        return False


def get_running_operations_from_schedule(part_number: str = None) -> List[Dict]:
    """
    Get operations that are currently running based on the latest schedule file.
    If part_number is provided, filter to only that part.
    """
    current_time = datetime.now()
    schedule_df = get_latest_schedule()

    if schedule_df.empty:
        return []

    # Filter operations that are currently active
    running_mask = (schedule_df['start_time'] <= current_time) & (schedule_df['end_time'] >= current_time)

    if part_number:
        part_mask = schedule_df['partno'] == part_number
        running_ops = schedule_df[running_mask & part_mask]
    else:
        running_ops = schedule_df[running_mask]

    running_operations = []

    with db_session:
        # Get machine details for better reporting
        machine_details = {}
        for machine in Machine.select():
            machine_details[machine.id] = {
                'name': f"{machine.work_center.code}-{machine.make}",
                'id': machine.id
            }

    # Convert to dictionary format
    for _, row in running_ops.iterrows():
        machine_id = row['machine_id']
        machine_name = machine_details.get(machine_id, {}).get('name', f"Machine-{machine_id}")

        # Calculate percent complete
        total_duration = (row['end_time'] - row['start_time']).total_seconds()
        elapsed = (current_time - row['start_time']).total_seconds()
        percent_complete = min(100, max(0, (elapsed / total_duration) * 100 if total_duration > 0 else 0))

        running_operations.append({
            'partno': row['partno'],
            'operation': row['operation'],
            'machine_id': machine_id,
            'machine_name': machine_name,
            'start_time': row['start_time'],
            'end_time': row['end_time'],
            'quantity': row['quantity'],
            'percent_complete': round(percent_complete, 2)
        })

    return running_operations


def get_running_operations_from_db(part_number: str = None) -> List[Dict]:
    """
    Get operations that are currently running based on the PlannedScheduleItem and ScheduleVersion tables.
    If part_number is provided, filter to only that part.
    """
    current_time = datetime.now()
    running_operations = []

    with db_session:
        # Get planned schedule items with active versions
        query = select((psi, sv) for psi in PlannedScheduleItem
                       for sv in psi.schedule_versions
                       if sv.is_active and
                       sv.planned_start_time <= current_time and
                       sv.planned_end_time >= current_time)

        if part_number:
            query = select((psi, sv) for psi in PlannedScheduleItem
                           for sv in psi.schedule_versions
                           if sv.is_active and
                           sv.planned_start_time <= current_time and
                           sv.planned_end_time >= current_time and
                           psi.order.part_number == part_number)

        results = list(query)

        # Get machine details for better reporting
        machine_details = {}
        for machine in Machine.select():
            machine_details[machine.id] = {
                'name': f"{machine.work_center.code}-{machine.make}",
                'id': machine.id
            }

        # Process results
        for psi, sv in results:
            machine_id = psi.machine.id
            machine_name = machine_details.get(machine_id, {}).get('name', f"Machine-{machine_id}")

            # Calculate percent complete
            total_duration = (sv.planned_end_time - sv.planned_start_time).total_seconds()
            elapsed = (current_time - sv.planned_start_time).total_seconds()
            percent_complete = min(100, max(0, (elapsed / total_duration) * 100 if total_duration > 0 else 0))

            running_operations.append({
                'partno': psi.order.part_number,
                'operation': psi.operation.operation_description or f"Operation {psi.operation.operation_number}",
                'machine_id': machine_id,
                'machine_name': machine_name,
                'start_time': sv.planned_start_time,
                'end_time': sv.planned_end_time,
                'quantity': str(sv.remaining_quantity),
                'percent_complete': round(percent_complete, 2),
                'schedule_id': psi.id,
                'version_id': sv.id
            })

    return running_operations


def get_running_operations(part_number: str = None) -> List[Dict]:
    """
    Get operations that are currently running by checking both database and schedule file.
    If part_number is provided, filter to only that part.
    """
    # Get running operations from database
    db_operations = get_running_operations_from_db(part_number)

    # Get running operations from schedule file as fallback
    if not db_operations:
        schedule_operations = get_running_operations_from_schedule(part_number)
        return schedule_operations

    return db_operations


def get_dependent_operations(part_number: str) -> List[Dict]:
    """
    Get operations that depend on the specified part number from the database.
    This checks for dependencies in the PlannedScheduleItem table.
    """
    dependent_operations = []

    with db_session:
        # Find all operations that belong to this part
        order = Order.select(lambda o: o.part_number == part_number).first()

        if not order:
            return []

        # Get all scheduled items for this order
        scheduled_items = list(PlannedScheduleItem.select(lambda p: p.order == order))

        # If there are no scheduled items, no dependencies
        if not scheduled_items:
            return []

        # Find all operations that might depend on these operations
        # In a real system with dependencies, you would check actual dependencies
        # For this example, we'll consider operations with future start times as dependent
        current_time = datetime.now()

        for scheduled_item in scheduled_items:
            for version in scheduled_item.schedule_versions:
                if version.is_active and version.planned_start_time > current_time:
                    dependent_operations.append({
                        'part_number': scheduled_item.order.part_number,
                        'operation': scheduled_item.operation.operation_description or f"Operation {scheduled_item.operation.operation_number}",
                        'scheduled_start': version.planned_start_time,
                        'scheduled_end': version.planned_end_time,
                        'status': 'Scheduled'
                    })

    return dependent_operations


def get_scheduled_items(part_number: str) -> List[Dict]:
    """
    Get all scheduled items (past, current, and future) for a part number.
    """
    scheduled_items = []

    with db_session:
        order = Order.select(lambda o: o.part_number == part_number).first()

        if not order:
            return []

        # Get all scheduled items for this order
        items = list(PlannedScheduleItem.select(lambda p: p.order == order))

        for item in items:
            # Get the active version
            active_version = None
            for version in item.schedule_versions:
                if version.is_active:
                    active_version = version
                    break

            if active_version:
                scheduled_items.append({
                    'id': item.id,
                    'operation': item.operation.operation_description or f"Operation {item.operation.operation_number}",
                    'machine': f"{item.machine.work_center.code}-{item.machine.make}",
                    'start_time': active_version.planned_start_time,
                    'end_time': active_version.planned_end_time,
                    'total_quantity': item.total_quantity,
                    'remaining_quantity': active_version.remaining_quantity,
                    'completed_quantity': active_version.completed_quantity,
                    'version': active_version.version_number,
                    'status': item.status or "Scheduled"
                })

    return scheduled_items


def check_priority_change_impact(part_number: str) -> Tuple[bool, str, List[Dict], List[Dict], List[Dict]]:
    """
    Check if changing priority for a part is possible and what impact it would have.

    Priority can only be changed for future operations. Operations that are already scheduled
    up to the current date/time must maintain their original priority.

    Returns:
        Tuple containing:
        - Boolean indicating if change is possible
        - Message explaining why if not possible
        - List of running operations for this part
        - List of dependent operations that would be affected
        - List of scheduled items for this part
    """
    current_time = datetime.now()

    # Get running operations for this part
    running_operations = get_running_operations(part_number)

    # Get dependent operations
    dependent_operations = get_dependent_operations(part_number)

    # Get all scheduled items
    scheduled_items = get_scheduled_items(part_number)

    # Filter for scheduled items that are in progress or scheduled to start before current time
    current_and_past_items = [
        item for item in scheduled_items
        if item['start_time'] <= current_time
    ]

    # Check if there are running operations
    if running_operations:
        return (
            False,
            f"Part {part_number} has {len(running_operations)} operations currently running",
            running_operations,
            dependent_operations,
            scheduled_items
        )

    # Check if there are any operations scheduled up to current time
    if current_and_past_items:
        return (
            False,
            f"Part {part_number} has {len(current_and_past_items)} operations scheduled up to current time. Priority can only be changed for future operations.",
            running_operations,
            dependent_operations,
            scheduled_items
        )

    # If we reach here, priority change is possible but might have impacts
    return True, "Priority change is possible", running_operations, dependent_operations, scheduled_items


@router.post("/update-priority/", response_model=PriorityUpdateResponse)
async def update_part_priority(priority_update: PriorityUpdateRequest, background_tasks: BackgroundTasks):
    """
    Update the priority of a part considering current planned schedules.

    The system checks the PlannedScheduleItem table for operations scheduled up to the
    current date and time. Priority changes will only affect future operations - operations
    scheduled to start before or at the current time will maintain their original priority.

    Priority changes are strictly forbidden if the part has any currently running operations.

    Args:
        priority_update: The request containing part_number and new_priority
        background_tasks: FastAPI background tasks for scheduling updates

    Returns:
        PriorityUpdateResponse containing details of the operation
    """
    try:
        part_number = priority_update.part_number
        new_priority = priority_update.new_priority
        force_update = priority_update.force

        with db_session:
            # Find the order associated with the part number
            order = Order.select(lambda o: o.part_number == part_number).first()

            if not order:
                raise HTTPException(
                    status_code=404,
                    detail=f"Part number {part_number} not found"
                )

            # Check if part has an associated project
            if not order.project:
                raise HTTPException(
                    status_code=404,
                    detail=f"No project associated with part number {part_number}"
                )

            # Store the old priority
            old_priority = order.project.priority

            # If new priority is the same as old, no need to update
            if old_priority == new_priority:
                return PriorityUpdateResponse(
                    part_number=part_number,
                    old_priority=old_priority,
                    new_priority=old_priority,
                    priority_changed=False,
                    message="New priority is the same as current priority, no update needed",
                    running_operations=[],
                    dependent_operations=[]
                )

            # Check impact of priority change
            can_change, message, running_ops, dependent_ops, scheduled_items = check_priority_change_impact(part_number)

            # Format running operations for response
            formatted_running_ops = [
                RunningOperation(
                    machine_id=op['machine_id'],
                    machine_name=op['machine_name'],
                    operation=op['operation'],
                    start_time=op['start_time'],
                    end_time=op['end_time'],
                    percent_complete=op['percent_complete']
                ) for op in running_ops
            ]

            # Format dependent operations for response
            formatted_dependent_ops = [
                DependentOperation(
                    part_number=op.get('part_number', ''),
                    operation=op.get('operation', ''),
                    scheduled_start=op.get('scheduled_start', datetime.now()),
                    scheduled_end=op.get('scheduled_end', datetime.now()),
                    status=op.get('status', '')
                ) for op in dependent_ops
            ]

            # STRICT RULE: If there are ANY running operations, priority change is not allowed
            # unless force flag is set
            if running_ops and not force_update:
                return PriorityUpdateResponse(
                    part_number=part_number,
                    old_priority=old_priority,
                    new_priority=None,
                    priority_changed=False,
                    running_operations=formatted_running_ops,
                    dependent_operations=formatted_dependent_ops,
                    message=f"Priority update not possible: Part {part_number} has {len(running_ops)} operations currently running. Cannot change priority until all operations complete or use force=true.",
                    details={
                        "running_operations_count": len(running_ops),
                        "dependent_operations_count": len(dependent_ops),
                        "scheduled_items_count": len(scheduled_items)
                    }
                )

            # If dependent operations exist and force is not set, we don't change
            if dependent_ops and not force_update:
                return PriorityUpdateResponse(
                    part_number=part_number,
                    old_priority=old_priority,
                    new_priority=None,
                    priority_changed=False,
                    running_operations=formatted_running_ops,
                    dependent_operations=formatted_dependent_ops,
                    message="Priority update not possible due to dependent operations. Use force=true to override.",
                    details={
                        "running_operations_count": len(running_ops),
                        "dependent_operations_count": len(dependent_ops),
                        "scheduled_items_count": len(scheduled_items)
                    }
                )

            # If we reach here, we can update the priority
            order.project.priority = new_priority
            commit()

            # Record the priority change in history
            record_priority_change(part_number, old_priority, new_priority)

            # Generate a new schedule in the background
            background_tasks.add_task(regenerate_schedule, current_time=datetime.now())

            # If we used force, provide a warning in the message
            message = "Priority updated successfully."
            if force_update and (running_ops or dependent_ops):
                message = "Priority updated successfully with force option. This may disrupt currently running operations or dependent operations."

            return PriorityUpdateResponse(
                part_number=part_number,
                old_priority=old_priority,
                new_priority=new_priority,
                priority_changed=True,
                running_operations=formatted_running_ops,
                dependent_operations=formatted_dependent_ops,
                message=message,
                details={
                    "running_operations_count": len(running_ops),
                    "dependent_operations_count": len(dependent_ops),
                    "scheduled_items_count": len(scheduled_items),
                    "schedule_regenerated": True,
                    "force_applied": force_update
                }
            )

    except Exception as e:
        print(f"Error updating priority: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


def regenerate_schedule(current_time=None):
    """
    Regenerate the production schedule, optionally from a specific time.
    This function also updates the PlannedScheduleItem and ScheduleVersion tables.

    When regenerating with current_time specified, only operations scheduled
    to start after current_time will be rescheduled with new priorities.
    Operations scheduled to start before or at current_time will maintain
    their original schedule.

    Args:
        current_time: The time from which to regenerate the schedule. If None,
                     the default start date will be used.

    Returns:
        Tuple containing schedule dataframe, overall end time, overall time,
        daily production, component status, and partially completed parts
    """
    df = fetch_operations()
    component_quantities = fetch_component_quantities()
    lead_times = fetch_lead_times()

    # Get current running operations if a current time is provided
    respect_running_operations = current_time is not None

    # If we're regenerating from current time, we need to preserve existing schedules
    # for operations up to current time
    existing_schedule_items = []
    if current_time:
        with db_session:
            # Get all scheduled items with start time <= current_time
            query = select((psi, sv) for psi in PlannedScheduleItem
                           for sv in psi.schedule_versions
                           if sv.is_active and sv.planned_start_time <= current_time)

            for psi, sv in query:
                existing_schedule_items.append({
                    'part_number': psi.order.part_number,
                    'operation_number': psi.operation.operation_number,
                    'machine_id': psi.machine.id,
                    'start_time': sv.planned_start_time,
                    'end_time': sv.planned_end_time,
                    'quantity': sv.remaining_quantity,
                    'priority': psi.order.project.priority,  # Preserve original priority
                    'preserve': True  # Flag to indicate this should not be rescheduled
                })

    # Generate the schedule
    schedule_df, overall_end_time, overall_time, daily_production, component_status, partially_completed = \
        schedule_operations(
            df,
            component_quantities,
            lead_times,
            current_time=current_time,
            respect_running_operations=respect_running_operations,
            existing_schedule_items=existing_schedule_items  # Pass preserved items to scheduling algorithm
        )

    # Save the latest schedule for future reference
    if not schedule_df.empty:
        save_latest_schedule(schedule_df)

        # Update database tables with new schedule
        with db_session:
            # For each row in the schedule
            for _, row in schedule_df.iterrows():
                part_number = row['partno']
                operation_number = int(row['operation_number']) if 'operation_number' in row else 0
                machine_id = row['machine_id']
                start_time = row['start_time']
                end_time = row['end_time']
                quantity = int(float(row['quantity'])) if isinstance(row['quantity'], str) else int(row['quantity'])

                # Check if this is a preserved item (should not create new version)
                is_preserved = row.get('preserve', False)

                # Find the order
                order = Order.select(lambda o: o.part_number == part_number).first()
                if not order:
                    continue

                # Find the operation
                operation = Operation.select(
                    lambda op: op.order == order and op.operation_number == operation_number).first()
                if not operation:
                    continue

                # Find the machine
                machine = Machine.get(id=machine_id)
                if not machine:
                    continue

                # Check if there's an existing planned schedule item
                planned_item = PlannedScheduleItem.select(
                    lambda p: p.order == order and p.operation == operation and p.machine == machine
                ).first()

                # If this is a preserved item and it already exists, skip it
                if is_preserved and planned_item:
                    continue

                if planned_item:
                    # Update existing item
                    new_version_number = planned_item.current_version + 1 if planned_item.current_version else 1
                    planned_item.current_version = new_version_number

                    # Mark all existing versions as inactive
                    for version in planned_item.schedule_versions:
                        if version.is_active:
                            version.is_active = False

                    # Create new version
                    new_version = ScheduleVersion(
                        schedule_item=planned_item,
                        version_number=new_version_number,
                        planned_start_time=start_time,
                        planned_end_time=end_time,
                        planned_quantity=quantity,
                        completed_quantity=0,
                        remaining_quantity=quantity,
                        is_active=True
                    )

                else:
                    # Create new planned schedule item
                    planned_item = PlannedScheduleItem(
                        order=order,
                        operation=operation,
                        machine=machine,
                        initial_start_time=start_time,
                        initial_end_time=end_time,
                        total_quantity=quantity,
                        remaining_quantity=quantity,
                        status="Scheduled",
                        current_version=1
                    )

                    # Create first version
                    new_version = ScheduleVersion(
                        schedule_item=planned_item,
                        version_number=1,
                        planned_start_time=start_time,
                        planned_end_time=end_time,
                        planned_quantity=quantity,
                        completed_quantity=0,
                        remaining_quantity=quantity,
                        is_active=True
                    )

            commit()

    return schedule_df, overall_end_time, overall_time, daily_production, component_status, partially_completed


@router.get("/schedule-batch/", response_model=ScheduleResponse)
async def schedule():
    """Generate a production schedule for all operations"""
    try:
        # Initialize work_centers_data
        work_centers_data = []

        with db_session:
            # Fetch work centers and their machines
            for work_center in WorkCenter.select():
                machines_in_wc = []
                for machine in work_center.machines:
                    machines_in_wc.append({
                        "id": str(machine.id),
                        "name": machine.make,
                        "model": machine.model,
                        "type": machine.type
                    })

                work_centers_data.append(
                    WorkCenterMachine(
                        work_center_code=work_center.code,
                        work_center_name=work_center.work_center_name or "",
                        machines=machines_in_wc
                    )
                )

        # Generate the schedule
        schedule_df, overall_end_time, overall_time, daily_production, component_status, partially_completed = \
            regenerate_schedule()

        scheduled_operations = []
        if not schedule_df.empty:
            with db_session:
                machine_details = {}
                for machine in Machine.select():
                    machine_name = f"{machine.work_center.code}-{machine.make}"
                    machine_details[machine.id] = {
                        'name': machine_name,
                        'id': machine.id
                    }

                orders_map = {order.part_number: order.production_order for order in Order.select()}

            for _, row in schedule_df.iterrows():
                machine_id = row['machine_id']
                machine_name = machine_details.get(machine_id, {'name': f'Machine-{machine_id}'})['name']

                scheduled_operations.append(
                    ScheduledOperation(
                        component=row['partno'],
                        description=row['operation'],
                        machine=machine_name,
                        start_time=row['start_time'],
                        end_time=row['end_time'],
                        quantity=row['quantity'],
                        production_order=orders_map.get(row['partno'], '')
                    )
                )

        return ScheduleResponse(
            scheduled_operations=scheduled_operations,
            overall_end_time=overall_end_time,
            overall_time=str(overall_time),
            daily_production=daily_production,
            component_status=component_status,
            partially_completed=partially_completed,
            work_centers=work_centers_data
        )

    except Exception as e:
        print(f"Error in schedule endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/machine_schedules/", response_model=MachineSchedulesOut)
async def get_machine_schedules(start_date: Optional[datetime] = None, end_date: Optional[datetime] = None):
    """Get schedules grouped by machine"""
    with db_session:
        # Try to get from database first
        machine_schedules = {}

        # Query active schedule versions
        query = select((psi, sv) for psi in PlannedScheduleItem for sv in psi.schedule_versions if sv.is_active)
        if start_date:
            query = select((psi, sv) for psi in PlannedScheduleItem for sv in psi.schedule_versions
                           if sv.is_active and sv.planned_end_time >= start_date)
        if end_date:
            query = select((psi, sv) for psi in PlannedScheduleItem for sv in psi.schedule_versions
                           if sv.is_active and sv.planned_start_time <= end_date)
        if start_date and end_date:
            query = select((psi, sv) for psi in PlannedScheduleItem for sv in psi.schedule_versions
                           if sv.is_active and sv.planned_end_time >= start_date and sv.planned_start_time <= end_date)

        results = list(query)

        if results:
            # Process from database
            for psi, sv in results:
                machine_name = f"{psi.machine.work_center.code}-{psi.machine.make}"

                if machine_name not in machine_schedules:
                    machine_schedules[machine_name] = []

                machine_schedules[machine_name].append({
                    "part_number": psi.order.part_number,
                    "operation": psi.operation.operation_description or f"Operation {psi.operation.operation_number}",
                    "start_time": sv.planned_start_time,
                    "end_time": sv.planned_end_time,
                    "duration_minutes": (sv.planned_end_time - sv.planned_start_time).total_seconds() / 60
                })
        else:
            # Fall back to CSV file if database has no results
            schedule_df = get_latest_schedule()

            if schedule_df.empty:
                # If no cached schedule exists, generate a new one
                schedule_df, _, _, _, _, _ = regenerate_schedule()

            if not schedule_df.empty:
                # Get machine names mapping with work center info
                machine_details = {}
                for machine in Machine.select():
                    machine_name = f"{machine.work_center.code}-{machine.make}"
                    machine_details[machine.id] = machine_name

                # Filter by date if necessary
                if start_date:
                    schedule_df = schedule_df[schedule_df['end_time'] >= start_date]
                if end_date:
                    schedule_df = schedule_df[schedule_df['start_time'] <= end_date]

                for _, row in schedule_df.iterrows():
                    machine_id = row['machine_id']
                    machine_name = machine_details.get(machine_id, f"Machine-{machine_id}")

                    if machine_name not in machine_schedules:
                        machine_schedules[machine_name] = []

                    machine_schedules[machine_name].append({
                        "part_number": row['partno'],
                        "operation": row['operation'],
                        "start_time": row['start_time'],
                        "end_time": row['end_time'],
                        "duration_minutes": (row['end_time'] - row['start_time']).total_seconds() / 60
                    })

        return MachineSchedulesOut(machine_schedules=machine_schedules)


@router.get("/priority-history/{part_number}")
async def get_priority_history(part_number: str):
    """Get the priority change history for a specific part number"""
    try:
        # from app.utils.schedule_helpers import PRIORITY_HISTORY_FILE
        import json
        import os

        if not os.path.exists(PRIORITY_HISTORY_FILE):
            return {"part_number": part_number, "history": []}

        with open(PRIORITY_HISTORY_FILE, 'r') as f:
            history = json.load(f)

        # Filter history for this part number
        part_history = [entry for entry in history if entry['part_number'] == part_number]

        return {"part_number": part_number, "history": part_history}

    except Exception as e:
        print(f"Error fetching priority history: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))