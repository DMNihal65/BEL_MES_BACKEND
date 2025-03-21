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
    scheduled_start: Optional[datetime] = None
    scheduled_end: Optional[datetime] = None
    project_start: Optional[datetime] = None
    project_end: Optional[datetime] = None
    project_delivery: Optional[datetime] = None
    is_changeable: bool
    scheduling_status: str
    reason: Optional[str] = None


class PriorityUpdateRequest(BaseModel):
    part_number: str
    new_priority: int


@db_session
def determine_scheduling_status(order, current_time):
    """
    Helper function to determine scheduling status and changeability
    Uses both schedule versions and project dates for more accurate status
    Returns a tuple of (earliest_start, latest_end, project_start, project_end, project_delivery,
                        scheduling_status, is_changeable, reason)

    Updated to allow priority changes for parts in production
    """
    try:
        # Default scheduling details
        earliest_start = None
        latest_end = None
        project_start = None
        project_end = None
        project_delivery = None
        scheduling_status = "Not Scheduled"
        is_changeable = True
        reason = "No scheduling constraints"

        if not order:
            return earliest_start, latest_end, project_start, project_end, project_delivery, scheduling_status, is_changeable, reason

        # Get project dates if available
        if order.project:
            project_start = order.project.start_date
            project_end = order.project.end_date
            project_delivery = order.project.delivery_date

        # Try to get scheduling information from PlannedScheduleItem
        schedule_items = select(psi for psi in PlannedScheduleItem if psi.order == order)

        if schedule_items.count() > 0:
            # Find earliest scheduled start and latest scheduled end
            for item in schedule_items:
                versions = select(sv for sv in ScheduleVersion
                                  if sv.schedule_item == item and sv.is_active)

                for version in versions:
                    if version.planned_start_time and (
                            earliest_start is None or version.planned_start_time < earliest_start):
                        earliest_start = version.planned_start_time

                    if version.planned_end_time and (latest_end is None or version.planned_end_time > latest_end):
                        latest_end = version.planned_end_time

            # Determine completion status
            completed_count = 0
            total_versions = 0

            for item in schedule_items:
                versions = select(sv for sv in ScheduleVersion
                                  if sv.schedule_item == item and sv.is_active)

                for version in versions:
                    total_versions += 1
                    if version.completed_quantity >= version.planned_quantity:
                        completed_count += 1

            # Determine schedule status based on dates and completion
            if total_versions > 0 and completed_count == total_versions:
                scheduling_status = "Completed"
                is_changeable = False
                reason = "Part is already completed"
            elif earliest_start and latest_end and earliest_start <= current_time and latest_end > current_time:
                scheduling_status = "In Progress"
                # Changed to allow priority updates for parts in production
                is_changeable = True
                reason = "Part is currently in production"
            elif earliest_start and earliest_start > current_time:
                days_until_start = (earliest_start - current_time).days
                scheduling_status = "Scheduled Future"
                is_changeable = True
                reason = f"Part is scheduled to start in the future ({days_until_start} days)"
            elif project_start and project_end:
                # Use project dates as fallback if no schedule items found
                if project_start <= current_time and project_end > current_time:
                    scheduling_status = "Project Active"
                    is_changeable = True
                    reason = "Part belongs to an active project"
                elif project_start > current_time:
                    scheduling_status = "Project Future"
                    is_changeable = True
                    reason = "Part belongs to a future project"
                else:
                    scheduling_status = "Project Ended"
                    is_changeable = False
                    reason = "Project end date has passed"
            else:
                scheduling_status = "Scheduled Today/Soon"
                is_changeable = True
                reason = "Part is scheduled to start soon"
        elif project_start and project_end:
            # Use project dates if no schedule items found
            if project_start <= current_time and project_end > current_time:
                scheduling_status = "Project Active"
                is_changeable = True
                reason = "Part belongs to an active project"
            elif project_start > current_time:
                scheduling_status = "Project Future"
                is_changeable = True
                reason = "Part belongs to a future project"
            else:
                scheduling_status = "Project Ended"
                is_changeable = False
                reason = "Project end date has passed"

        return earliest_start, latest_end, project_start, project_end, project_delivery, scheduling_status, is_changeable, reason

    except Exception as e:
        # If any error occurs, return default values with error information
        return None, None, None, None, None, "Error", True, f"Error determining status: {str(e)}"


@router.get("/details", response_model=List[PriorityDetails])
@db_session
def get_priority_details():
    """
    Get comprehensive priority details for all active parts
    Includes scheduling information and priority changeability
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

                # Get current project priority
                current_priority = order.project.priority if order and order.project else 999

                # Get scheduling status using helper function
                earliest_start, latest_end, project_start, project_end, project_delivery, scheduling_status, is_changeable, reason = determine_scheduling_status(
                    order, current_time)

                priority_details.append(PriorityDetails(
                    part_number=part_number,
                    current_priority=current_priority,
                    current_status=part_status.status,
                    scheduled_start=earliest_start,
                    scheduled_end=latest_end,
                    project_start=project_start,
                    project_end=project_end,
                    project_delivery=project_delivery,
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

        # Get current project priority
        current_priority = order.project.priority if order.project else 999

        # Get scheduling status using helper function
        earliest_start, latest_end, project_start, project_end, project_delivery, scheduling_status, is_changeable, reason = determine_scheduling_status(
            order, current_time)

        return PriorityDetails(
            part_number=part_number,
            current_priority=current_priority,
            current_status=part_status.status,
            scheduled_start=earliest_start,
            scheduled_end=latest_end,
            project_start=project_start,
            project_end=project_end,
            project_delivery=project_delivery,
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
    Update part priority based on schedule version's planned start/end times
    - Only prevents changes for currently scheduled or past scheduled parts
    - Allows changes for parts scheduled in the future
    """
    try:
        current_time = datetime.now()

        # Find the order
        order = Order.select(lambda o: o.part_number == update_request.part_number).first()
        if not order:
            raise HTTPException(status_code=404, detail=f"Part {update_request.part_number} not found")

        # Check part status
        part_status = PartScheduleStatus.select(
            lambda p: p.part_number == update_request.part_number
        ).first()

        if not part_status or part_status.status != 'active':
            raise HTTPException(
                status_code=400,
                detail="Part is not active for priority update"
            )

        # Check schedule versions directly to determine if priority can be changed
        schedule_items = select(psi for psi in PlannedScheduleItem if psi.order == order)

        # Initialize flags to track schedule status
        is_currently_scheduled = False
        is_past_scheduled = False
        earliest_future_start = None

        for item in schedule_items:
            versions = select(sv for sv in ScheduleVersion
                              if sv.schedule_item == item and sv.is_active)

            for version in versions:
                # If we have start and end times, check scheduling status
                if version.planned_start_time and version.planned_end_time:
                    # Part is currently scheduled
                    if version.planned_start_time <= current_time <= version.planned_end_time:
                        is_currently_scheduled = True

                    # Part was scheduled in the past
                    elif version.planned_end_time < current_time:
                        is_past_scheduled = True

                    # Part is scheduled for the future
                    elif version.planned_start_time > current_time:
                        if earliest_future_start is None or version.planned_start_time < earliest_future_start:
                            earliest_future_start = version.planned_start_time

        # Check if priority change is allowed based on schedule
        if is_currently_scheduled:
            raise HTTPException(
                status_code=400,
                detail="Cannot change priority for a part that is currently scheduled"
            )

        if is_past_scheduled:
            raise HTTPException(
                status_code=400,
                detail="Cannot change priority for a part that was scheduled in the past"
            )

        # Get current priority
        old_priority = order.project.priority if order.project else 999

        # Update priority
        try:
            if not order.project:
                # Create a new project if none exists
                project = Project(
                    name=f"Project for {update_request.part_number}",
                    priority=update_request.new_priority,
                    start_date=current_time,
                    end_date=current_time + timedelta(days=30),
                    delivery_date=current_time + timedelta(days=30)
                )
                order.project = project
            else:
                # Update priority
                order.project.priority = update_request.new_priority

            # Commit changes
            commit()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error updating priority: {str(e)}")

        # Get scheduling information after update for response
        # Note: We're no longer using determine_scheduling_status here since
        # we've already performed our custom scheduling checks
        earliest_start = None
        latest_end = None
        project_start = None
        project_end = None
        project_delivery = None

        # Get project dates
        if order.project:
            project_start = order.project.start_date
            project_end = order.project.end_date
            project_delivery = order.project.delivery_date

        # Get earliest start and latest end from schedule versions
        for item in schedule_items:
            versions = select(sv for sv in ScheduleVersion
                              if sv.schedule_item == item and sv.is_active)

            for version in versions:
                if version.planned_start_time and (earliest_start is None or
                                                   version.planned_start_time < earliest_start):
                    earliest_start = version.planned_start_time

                if version.planned_end_time and (latest_end is None or
                                                 version.planned_end_time > latest_end):
                    latest_end = version.planned_end_time

        # Determine scheduling status based on our calculations
        if earliest_future_start:
            scheduling_status = "Scheduled Future"
            days_until_start = (earliest_future_start - current_time).days
            reason = f"Part is scheduled to start in the future ({days_until_start} days)"
        elif not schedule_items.count():
            scheduling_status = "Not Scheduled"
            reason = "Part is not scheduled yet"
        else:
            scheduling_status = "Scheduling Checked"
            reason = "No scheduling conflicts found"

        return PriorityDetails(
            part_number=update_request.part_number,
            current_priority=update_request.new_priority,
            current_status=part_status.status,
            scheduled_start=earliest_start,
            scheduled_end=latest_end,
            project_start=project_start,
            project_end=project_end,
            project_delivery=project_delivery,
            is_changeable=True,
            scheduling_status=scheduling_status,
            reason=f"Priority successfully updated from {old_priority} to {update_request.new_priority}"
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

                # Get current project priority
                current_priority = order.project.priority if order.project else 999

                # Get scheduling status using helper function
                earliest_start, latest_end, project_start, project_end, project_delivery, scheduling_status, is_changeable, reason = determine_scheduling_status(
                    order, current_time)

                # Only include changeable parts
                if is_changeable:
                    priority_details.append(PriorityDetails(
                        part_number=part_number,
                        current_priority=current_priority,
                        current_status=part_status.status,
                        scheduled_start=earliest_start,
                        scheduled_end=latest_end,
                        project_start=project_start,
                        project_end=project_end,
                        project_delivery=project_delivery,
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

# Include this router in your main FastAPI app
# app.include_router(router)