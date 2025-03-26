from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from pony.orm import db_session, select, commit

from app.models import Order, Project, PlannedScheduleItem, PartScheduleStatus, ScheduleVersion

router = APIRouter(prefix="/priority", tags=["priority"])


class PriorityDetails(BaseModel):
    part_number: str
    current_priority: int
    current_status: str
    planned_start_time: Optional[datetime] = None
    planned_end_time: Optional[datetime] = None
    is_changeable: bool
    scheduling_status: str
    reason: Optional[str] = None


class PriorityUpdateRequest(BaseModel):
    part_number: str
    new_priority: int


class ProjectPriorityUpdateRequest(BaseModel):
    priority: int


@db_session
def determine_scheduling_status(order, current_time):
    """
    Helper function to determine scheduling status and changeability
    Uses schedule versions' planned start and end times
    Returns a tuple of (planned_start_time, planned_end_time, scheduling_status, is_changeable, reason)
    """
    try:
        # Default scheduling details
        planned_start_time = None
        planned_end_time = None
        scheduling_status = "Not Scheduled"
        is_changeable = True
        reason = "No scheduling constraints"

        if not order:
            return planned_start_time, planned_end_time, scheduling_status, is_changeable, reason

        # Try to get scheduling information from PlannedScheduleItem
        schedule_items = select(psi for psi in PlannedScheduleItem if psi.order == order)

        if schedule_items.count() > 0:
            # Find earliest planned start and latest planned end
            earliest_start = None
            latest_end = None

            for item in schedule_items:
                # Get active schedule versions for this item
                versions = select(sv for sv in ScheduleVersion
                                  if sv.schedule_item == item and sv.is_active)

                for version in versions:
                    # Verify datetime fields are valid before comparison
                    if version.planned_start_time:
                        # Make sure we have a valid datetime object
                        if isinstance(version.planned_start_time, datetime):
                            if earliest_start is None or version.planned_start_time < earliest_start:
                                earliest_start = version.planned_start_time
                        else:
                            # Log error or handle invalid date format
                            pass

                    if version.planned_end_time:
                        # Make sure we have a valid datetime object
                        if isinstance(version.planned_end_time, datetime):
                            if latest_end is None or version.planned_end_time > latest_end:
                                latest_end = version.planned_end_time
                        else:
                            # Log error or handle invalid date format
                            pass

            # Update with validated dates
            planned_start_time = earliest_start
            planned_end_time = latest_end

            # Determine completion status based on all scheduled items being completed
            completed_count = 0
            total_versions = 0

            for item in schedule_items:
                versions = select(sv for sv in ScheduleVersion
                                  if sv.schedule_item == item and sv.is_active)

                for version in versions:
                    total_versions += 1
                    if version.completed_quantity and version.completed_quantity >= version.planned_quantity:
                        completed_count += 1

            # Determine schedule status based on dates and completion
            if total_versions > 0 and completed_count == total_versions:
                scheduling_status = "Completed"
                is_changeable = False
                reason = "Part is already completed"
            elif planned_start_time and planned_end_time:
                if planned_end_time < current_time:
                    scheduling_status = "Past Due"
                    is_changeable = False
                    reason = "Part production window has passed"
                elif planned_start_time <= current_time and planned_end_time > current_time:
                    scheduling_status = "In Progress"
                    is_changeable = True
                    reason = "Part is currently in production"
                elif planned_start_time > current_time:
                    days_until_start = (planned_start_time - current_time).days
                    scheduling_status = "Scheduled Future"
                    is_changeable = True
                    reason = f"Part is scheduled to start in the future ({days_until_start} days)"
                else:
                    scheduling_status = "Scheduled Today/Soon"
                    is_changeable = True
                    reason = "Part is scheduled to start soon"
            else:
                scheduling_status = "Not Scheduled"
                is_changeable = True
                reason = "Part is not yet scheduled"

        return planned_start_time, planned_end_time, scheduling_status, is_changeable, reason

    except Exception as e:
        # If any error occurs, return default values with error information
        return None, None, "Error", True, f"Error determining status: {str(e)}"


@router.get("/details", response_model=List[PriorityDetails])
@db_session
def get_priority_details():
    """
    Get comprehensive priority details for all active parts
    Includes scheduling information from planned times
    """
    try:
        current_time = datetime.now()
        priority_details = []

        # Get all active parts with error handling
        try:
            active_parts = list(select(ps for ps in PartScheduleStatus if ps.status == 'active'))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error retrieving active parts: {str(e)}")

        for part_status in active_parts:
            try:
                part_number = part_status.part_number

                # Find the order
                order = Order.select(lambda o: o.part_number == part_number).first()
                if not order:
                    continue

                # Get current project priority directly from the order's project
                current_priority = order.project.priority if order and order.project else 0

                # Get scheduling status using helper function
                planned_start_time, planned_end_time, scheduling_status, is_changeable, reason = determine_scheduling_status(
                    order, current_time)

                # Double-check that dates are valid before including in response
                if planned_start_time and not isinstance(planned_start_time, datetime):
                    planned_start_time = None

                if planned_end_time and not isinstance(planned_end_time, datetime):
                    planned_end_time = None

                priority_details.append(PriorityDetails(
                    part_number=part_number,
                    current_priority=current_priority,
                    current_status=part_status.status,
                    planned_start_time=planned_start_time,
                    planned_end_time=planned_end_time,
                    is_changeable=is_changeable,
                    scheduling_status=scheduling_status,
                    reason=reason
                ))
            except Exception as e:
                # Skip this part if there's an error processing it
                continue

        # Sort by priority
        priority_details.sort(key=lambda x: x.current_priority)

        return priority_details
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving priority details: {str(e)}")


@router.get("/details/{part_number}", response_model=PriorityDetails)
@db_session
def get_single_part_priority(part_number: str):
    """
    Get priority details for a specific part
    """
    try:
        current_time = datetime.now()

        # Find the order and its project
        order = Order.select(lambda o: o.part_number == part_number).first()
        if not order:
            raise HTTPException(status_code=404, detail=f"Part {part_number} not found")

        # Get part status
        part_status = PartScheduleStatus.select(
            lambda p: p.part_number == part_number
        ).first()

        if not part_status:
            raise HTTPException(status_code=404, detail=f"Part status for {part_number} not found")

        # Get current project priority directly from the order's project
        current_priority = order.project.priority if order.project else 0

        # Get scheduling status using helper function
        planned_start_time, planned_end_time, scheduling_status, is_changeable, reason = determine_scheduling_status(
            order, current_time)

        # Validate datetime objects before returning
        if planned_start_time and not isinstance(planned_start_time, datetime):
            planned_start_time = None

        if planned_end_time and not isinstance(planned_end_time, datetime):
            planned_end_time = None

        return PriorityDetails(
            part_number=part_number,
            current_priority=current_priority,
            current_status=part_status.status,
            planned_start_time=planned_start_time,
            planned_end_time=planned_end_time,
            is_changeable=is_changeable,
            scheduling_status=scheduling_status,
            reason=reason
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving part priority: {str(e)}")


@router.put("/update", response_model=PriorityDetails)
@db_session
def update_part_priority(update_request: PriorityUpdateRequest):
    """
    Update priority for a specific part by part number
    Only allows changes for parts that are not completed or past due
    """
    try:
        current_time = datetime.now()
        part_number = update_request.part_number
        new_priority = update_request.new_priority

        # Find the order
        order = Order.select(lambda o: o.part_number == part_number).first()
        if not order:
            raise HTTPException(status_code=404, detail=f"Part {part_number} not found")

        # Get scheduling status
        planned_start_time, planned_end_time, scheduling_status, is_changeable, reason = determine_scheduling_status(
            order, current_time)

        # Check if part can have its priority changed
        if not is_changeable:
            raise HTTPException(status_code=400,
                                detail=f"Cannot change priority for this part: {reason}")

        # Get current part status
        part_status = PartScheduleStatus.select(lambda p: p.part_number == part_number).first()
        if not part_status or part_status.status != 'active':
            raise HTTPException(status_code=400, detail="Part is not active for priority update")

        # Get the old priority before updating
        old_priority = 0
        if order.project:
            old_priority = order.project.priority

            # Only update if the priority is actually changing
            if old_priority == new_priority:
                return PriorityDetails(
                    part_number=part_number,
                    current_priority=old_priority,
                    current_status=part_status.status,
                    planned_start_time=planned_start_time,
                    planned_end_time=planned_end_time,
                    is_changeable=is_changeable,
                    scheduling_status=scheduling_status,
                    reason="Priority unchanged (same as current)"
                )

            # Get all projects ordered by priority for reordering
            all_projects = select(p for p in Project).order_by(Project.priority)[:]

            # If moving to a higher priority (lower number)
            if new_priority < old_priority:
                # Shift down projects that are between new and old priority
                for project in all_projects:
                    if project.id != order.project.id and new_priority <= project.priority < old_priority:
                        project.priority += 1

            # If moving to a lower priority (higher number)
            elif new_priority > old_priority:
                # Shift up projects that are between old and new priority
                for project in all_projects:
                    if project.id != order.project.id and old_priority < project.priority <= new_priority:
                        project.priority -= 1

            # Set the new priority for the current project
            order.project.priority = new_priority
        else:
            # Create a new project if none exists
            order.project = Project(
                name=f"Project {order.part_number}",
                priority=new_priority,
                start_date=current_time,
                end_date=current_time + timedelta(days=30),
                delivery_date=current_time + timedelta(days=30)
            )

        commit()

        # Get the actual new priority (in case it was adjusted)
        current_priority = order.project.priority if order.project else new_priority

        # Get up-to-date scheduling information for response
        updated_start, updated_end, updated_status, updated_changeable, updated_reason = determine_scheduling_status(
            order, current_time)

        # Validate datetime objects
        if updated_start and not isinstance(updated_start, datetime):
            updated_start = None

        if updated_end and not isinstance(updated_end, datetime):
            updated_end = None

        return PriorityDetails(
            part_number=part_number,
            current_priority=current_priority,
            current_status=part_status.status,
            planned_start_time=updated_start,
            planned_end_time=updated_end,
            is_changeable=updated_changeable,
            scheduling_status=updated_status,
            reason=f"Priority updated from {old_priority} to {current_priority}"
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating priority: {str(e)}")


@router.put("/order/{order_id}/priority", response_model=PriorityDetails)
@db_session
def update_order_priority(order_id: int, priority_data: ProjectPriorityUpdateRequest):
    """
    Update priority for an order by ID, with completion status checks
    Only allowing changes for parts that are not completed or past due
    """
    try:
        current_time = datetime.now()
        new_priority = priority_data.priority

        # Find the order by ID
        order = Order.get(id=order_id)
        if not order:
            raise HTTPException(status_code=404, detail=f"Order with ID {order_id} not found")

        # Check part status
        part_status = PartScheduleStatus.select(
            lambda p: p.part_number == order.part_number
        ).first()

        if not part_status or part_status.status != 'active':
            raise HTTPException(
                status_code=400,
                detail="Part is not active for priority update"
            )

        # Get scheduling status using helper function
        planned_start_time, planned_end_time, scheduling_status, is_changeable, reason = determine_scheduling_status(
            order, current_time)

        # Check if part can have its priority changed
        if not is_changeable:
            raise HTTPException(status_code=400,
                                detail=f"Cannot change priority for this part: {reason}")

        # Get the old priority before updating
        old_priority = 0

        # Check if order has an associated project
        if not order.project:
            # Create a new project if none exists
            project = Project(
                name=f"Project for {order.part_number}",
                priority=new_priority,
                start_date=current_time,
                end_date=current_time + timedelta(days=30),
                delivery_date=current_time + timedelta(days=30)
            )
            order.project = project
        else:
            current_project = order.project
            old_priority = current_project.priority

            # If the priority is the same, no change needed
            if old_priority == new_priority:
                return PriorityDetails(
                    part_number=order.part_number,
                    current_priority=old_priority,
                    current_status=part_status.status if part_status else "unknown",
                    planned_start_time=planned_start_time,
                    planned_end_time=planned_end_time,
                    is_changeable=is_changeable,
                    scheduling_status=scheduling_status,
                    reason="Priority unchanged (same as current)"
                )

            # Get all projects ordered by priority
            all_projects = select(p for p in Project).order_by(Project.priority)[:]

            # Moving to a higher priority (lower number)
            if new_priority < old_priority:
                # Shift down projects that are between new and old priority
                for project in all_projects:
                    if project.id != current_project.id and new_priority <= project.priority < old_priority:
                        project.priority += 1

            # Moving to a lower priority (higher number)
            elif new_priority > old_priority:
                # Shift up projects that are between old and new priority
                for project in all_projects:
                    if project.id != current_project.id and old_priority < project.priority <= new_priority:
                        project.priority -= 1

            # Set the new priority for the current project
            current_project.priority = new_priority

        # Commit changes
        commit()

        # Get the actual new priority (in case it was adjusted during reordering)
        current_priority = order.project.priority if order.project else new_priority

        # Get up-to-date scheduling information for response
        updated_start, updated_end, updated_status, updated_changeable, updated_reason = determine_scheduling_status(
            order, current_time)

        # Validate datetime objects
        if updated_start and not isinstance(updated_start, datetime):
            updated_start = None

        if updated_end and not isinstance(updated_end, datetime):
            updated_end = None

        return PriorityDetails(
            part_number=order.part_number,
            current_priority=current_priority,
            current_status=part_status.status if part_status else "unknown",
            planned_start_time=updated_start,
            planned_end_time=updated_end,
            is_changeable=updated_changeable,
            scheduling_status=updated_status,
            reason=f"Priority successfully updated from {old_priority} to {current_priority}"
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error during priority update: {str(e)}")


@router.get("/changeable", response_model=List[PriorityDetails])
@db_session
def get_changeable_parts():
    """
    Get only parts that can have their priority changed
    """
    try:
        current_time = datetime.now()
        priority_details = []

        # Get all active parts
        active_parts = list(select(ps for ps in PartScheduleStatus if ps.status == 'active'))

        for part_status in active_parts:
            try:
                part_number = part_status.part_number

                # Find the order and its project
                order = Order.select(lambda o: o.part_number == part_number).first()
                if not order:
                    continue

                # Get current project priority directly from order's project
                current_priority = order.project.priority if order.project else 0

                # Get scheduling status using helper function
                planned_start_time, planned_end_time, scheduling_status, is_changeable, reason = determine_scheduling_status(
                    order, current_time)

                # Validate datetime objects
                if planned_start_time and not isinstance(planned_start_time, datetime):
                    planned_start_time = None

                if planned_end_time and not isinstance(planned_end_time, datetime):
                    planned_end_time = None

                # Only include changeable parts
                if is_changeable:
                    priority_details.append(PriorityDetails(
                        part_number=part_number,
                        current_priority=current_priority,
                        current_status=part_status.status,
                        planned_start_time=planned_start_time,
                        planned_end_time=planned_end_time,
                        is_changeable=is_changeable,
                        scheduling_status=scheduling_status,
                        reason=reason
                    ))
            except Exception as e:
                # Skip this part if there's an error processing it
                continue

        # Sort by priority
        priority_details.sort(key=lambda x: x.current_priority)

        return priority_details
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving changeable parts: {str(e)}")