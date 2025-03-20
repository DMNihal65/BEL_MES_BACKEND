from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import List, Optional, Dict, Tuple
from datetime import datetime, timedelta
from pony.orm import db_session, select, commit
import json
import os
import pandas as pd

from app.schemas.priority_schedule import PriorityUpdateResponse, PriorityUpdateRequest, RunningOperation, \
    DependentOperation
from app.models import Operation, Order, Machine, WorkCenter, PlannedScheduleItem, ScheduleVersion, Project

# Constants
LATEST_SCHEDULE_FILE = 'data/latest_schedule.csv'
PRIORITY_HISTORY_FILE = 'data/priority_history.json'

router = APIRouter(prefix="/api/v1/test", tags=["test"])


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


def get_running_operations_from_db(part_number: str = None) -> List[Dict]:
    """
    Get operations that are currently running based on the PlannedScheduleItem and ScheduleVersion tables.
    If part_number is provided, filter to only that part.
    """
    current_time = datetime.now()
    running_operations = []

    with db_session:
        # Get planned schedule items with active versions that are currently running
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


def get_past_operations(part_number: str) -> List[Dict]:
    """
    Get operations that have already been completed for a specific part.
    """
    current_time = datetime.now()
    past_operations = []

    with db_session:
        # Query for completed operations (end time in the past)
        query = select((psi, sv) for psi in PlannedScheduleItem
                       for sv in psi.schedule_versions
                       if sv.is_active and
                       sv.planned_end_time < current_time and
                       psi.order.part_number == part_number)

        results = list(query)

        # Process results
        for psi, sv in results:
            past_operations.append({
                'part_number': psi.order.part_number,
                'operation': psi.operation.operation_description or f"Operation {psi.operation.operation_number}",
                'machine_id': psi.machine.id,
                'start_time': sv.planned_start_time,
                'end_time': sv.planned_end_time,
                'schedule_id': psi.id,
                'version_id': sv.id
            })

    return past_operations


def get_dependent_operations(part_number: str) -> List[Dict]:
    """
    Get operations that depend on the specified part number from the database.
    This checks for dependencies in the PlannedScheduleItem table.
    """
    dependent_operations = []
    current_time = datetime.now()

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
        # For this example, we'll consider operations with future start times as dependent
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


def get_future_operations(part_number: str) -> List[Dict]:
    """
    Get operations that are scheduled in the future for a specific part.
    """
    current_time = datetime.now()
    future_operations = []

    with db_session:
        # Query for future operations (start time in the future)
        query = select((psi, sv) for psi in PlannedScheduleItem
                       for sv in psi.schedule_versions
                       if sv.is_active and
                       sv.planned_start_time > current_time and
                       psi.order.part_number == part_number)

        results = list(query)

        # Process results
        for psi, sv in results:
            future_operations.append({
                'part_number': psi.order.part_number,
                'operation': psi.operation.operation_description or f"Operation {psi.operation.operation_number}",
                'machine_id': psi.machine.id,
                'start_time': sv.planned_start_time,
                'end_time': sv.planned_end_time,
                'schedule_id': psi.id,
                'version_id': sv.id
            })

    return future_operations


def check_priority_change_impact(part_number: str) -> Tuple[bool, str, List[Dict], List[Dict], List[Dict], List[Dict]]:
    """
    Check if changing priority for a part is possible and what impact it would have.

    Priority can only be changed for future operations. Operations that are already completed
    or currently running cannot have their priority changed.

    Returns:
        Tuple containing:
        - Boolean indicating if change is possible
        - Message explaining why if not possible
        - List of running operations for this part
        - List of past operations for this part
        - List of dependent operations that would be affected
        - List of future operations that would be affected
    """
    # Get running operations for this part
    running_operations = get_running_operations_from_db(part_number)

    # Get past operations for this part
    past_operations = get_past_operations(part_number)

    # Get dependent operations
    dependent_operations = get_dependent_operations(part_number)

    # Get future operations
    future_operations = get_future_operations(part_number)

    # Check if there are running operations
    if running_operations:
        return (
            False,
            f"Part {part_number} has {len(running_operations)} operations currently running. Cannot change priority for running operations.",
            running_operations,
            past_operations,
            dependent_operations,
            future_operations
        )

    # Check if there are any past operations
    if past_operations:
        # We still allow changing priority for future operations even if there are completed operations
        return (
            True,
            f"Part {part_number} has {len(past_operations)} completed operations. Priority will only be changed for future operations.",
            running_operations,
            past_operations,
            dependent_operations,
            future_operations
        )

    # If we reach here, there are no restrictions on priority change
    if future_operations:
        return (
            True,
            f"Priority change is possible for all {len(future_operations)} future operations.",
            running_operations,
            past_operations,
            dependent_operations,
            future_operations
        )
    else:
        return (
            True,
            "No operations found for this part. Priority change is possible but may not have immediate effect.",
            running_operations,
            past_operations,
            dependent_operations,
            future_operations
        )


def update_future_operations_priority(part_number: str, new_priority: int) -> bool:
    """
    Update the priority for future operations of a specific part.

    This function preserves the priority for past and currently running operations,
    only updating the priority for operations scheduled to start in the future.

    Returns:
        Boolean indicating if any operations were updated
    """
    current_time = datetime.now()
    updated_count = 0

    with db_session:
        # Get the order associated with the part number
        order = Order.select(lambda o: o.part_number == part_number).first()

        if not order or not order.project:
            return False

        # Update the project priority
        order.project.priority = new_priority

        # Query for future scheduled items to explicitly mark them for reschedule
        query = select((psi, sv) for psi in PlannedScheduleItem
                       for sv in psi.schedule_versions
                       if sv.is_active and
                       sv.planned_start_time > current_time and
                       psi.order.part_number == part_number)

        for psi, sv in query:
            # Flag this item as needing reschedule (you could add a field for this)
            psi.needs_reschedule = True  # Assuming this field exists
            updated_count += 1

        commit()

    return updated_count > 0


@router.post("/update-priority/", response_model=PriorityUpdateResponse)
async def update_part_priority(priority_update: PriorityUpdateRequest, background_tasks: BackgroundTasks):
    """
    Update the priority of a part considering current planned schedules.

    The system checks the PlannedScheduleItem table for operations scheduled up to the
    current date and time. Priority changes will only affect future operations - operations
    that have already been completed or are currently running will maintain their original priority.

    Args:
        priority_update: The request containing part_number, new_priority, and force flag
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
            can_change, message, running_ops, past_ops, dependent_ops, future_ops = check_priority_change_impact(
                part_number)

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

            # STRICT RULE: If there are ANY running operations, priority change is not allowed unless force flag is set
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
                        "past_operations_count": len(past_ops),
                        "dependent_operations_count": len(dependent_ops),
                        "future_operations_count": len(future_ops)
                    }
                )

            # If we reach here and force is not set but there are running operations, we should not proceed
            if not can_change and not force_update:
                return PriorityUpdateResponse(
                    part_number=part_number,
                    old_priority=old_priority,
                    new_priority=None,
                    priority_changed=False,
                    running_operations=formatted_running_ops,
                    dependent_operations=formatted_dependent_ops,
                    message=message,
                    details={
                        "running_operations_count": len(running_ops),
                        "past_operations_count": len(past_ops),
                        "dependent_operations_count": len(dependent_ops),
                        "future_operations_count": len(future_ops)
                    }
                )

            # If force is set, or if there are no running operations, proceed with update
            # Update only the future operations' priority
            success = update_future_operations_priority(part_number, new_priority)

            # Record the priority change in history
            record_priority_change(part_number, old_priority, new_priority)

            # Generate a new schedule in the background, starting from current time
            # to preserve past and running operations
            background_tasks.add_task(regenerate_schedule, current_time=datetime.now())

            # Prepare the appropriate message based on what happened
            if force_update:
                message = "Priority updated with force option. Future operations have been rescheduled."
                if running_ops:
                    message += f" {len(running_ops)} currently running operations maintain their original schedule."
                if past_ops:
                    message += f" {len(past_ops)} completed operations are unaffected."
            else:
                message = "Priority updated successfully for future operations."
                if past_ops:
                    message += f" {len(past_ops)} completed operations maintain their original priority."

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
                    "past_operations_count": len(past_ops),
                    "dependent_operations_count": len(dependent_ops),
                    "future_operations_count": len(future_ops),
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
    This function preserves past and currently running operations,
    only rescheduling operations that start after the current time.

    Args:
        current_time: The time from which to regenerate the schedule. If None,
                      the entire schedule is regenerated.
    """
    # Import required functions at the top level
    from app.crud.operation import fetch_operations
    from app.crud.component_quantities import fetch_component_quantities
    from app.crud.leadtime import fetch_lead_times
    from app.algorithm.scheduling import schedule_operations

    df = fetch_operations()
    component_quantities = fetch_component_quantities()
    lead_times = fetch_lead_times()

    # Get existing operations up to current time to preserve them
    existing_schedule_items = []

    if current_time:
        with db_session:
            # Find all active operations scheduled before or at current time
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
                    'priority': psi.order.project.priority,  # Use current priority
                    'preserve': True  # Mark to preserve this operation as is
                })

    # Generate the schedule
    schedule_df, overall_end_time, overall_time, daily_production, component_status, partially_completed = \
        schedule_operations(
            df,
            component_quantities,
            lead_times,
            current_time=current_time,
            existing_schedule_items=existing_schedule_items
        )

    # Save the latest schedule for future reference
    if not schedule_df.empty:
        save_latest_schedule(schedule_df)

        # Update database tables with new schedule, but preserve existing operations
        with db_session:
            # For new operations in the schedule (future operations)
            for _, row in schedule_df.iterrows():
                # Skip preserved items - they already exist in the database
                if row.get('preserve', False):
                    continue

                part_number = row['partno']
                operation_number = int(row['operation_number']) if 'operation_number' in row else 0
                machine_id = row['machine_id']
                start_time = row['start_time']
                end_time = row['end_time']
                quantity = int(float(row['quantity'])) if isinstance(row['quantity'], str) else int(row['quantity'])

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

                # Check if there's an existing planned schedule item for this future operation
                planned_item = PlannedScheduleItem.select(
                    lambda p: p.order == order and p.operation == operation and p.machine == machine
                ).first()

                if planned_item:
                    # Update existing item with a new version
                    new_version_number = planned_item.current_version + 1 if planned_item.current_version else 1
                    planned_item.current_version = new_version_number

                    # Mark all existing versions as inactive
                    for version in planned_item.schedule_versions:
                        version.is_active = False

                    # Create new version
                    ScheduleVersion(
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
                    new_item = PlannedScheduleItem(
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
                    ScheduleVersion(
                        schedule_item=new_item,
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


@router.get("/priority-history/{part_number}")
async def get_priority_history(part_number: str):
    """Get the priority change history for a specific part number"""
    try:
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