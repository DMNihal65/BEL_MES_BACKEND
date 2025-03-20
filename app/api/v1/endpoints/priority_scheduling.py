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
    is_changeable: bool
    scheduling_status: str


class PriorityUpdateRequest(BaseModel):
    part_number: str
    new_priority: int


@router.get("/details", response_model=List[PriorityDetails])
@db_session
def get_priority_details():
    """
    Get comprehensive priority details for all active parts
    Includes scheduling information and priority changeability
    """
    current_time = datetime.now()

    # Get all active parts
    active_parts = select(ps for ps in PartScheduleStatus if ps.status == 'active')

    priority_details = []

    for part_status in active_parts:
        part_number = part_status.part_number

        # Find the order and its project
        order = Order.select(lambda o: o.part_number == part_number).first()
        if not order:
            continue

        # Get current project priority
        current_priority = order.project.priority if order.project else 999

        # Check scheduled items
        scheduled_items = select(
            psi for psi in PlannedScheduleItem
            if psi.order == order
        )

        # Determine scheduling details
        earliest_start = None
        latest_end = None
        scheduling_status = "Not Scheduled"
        is_changeable = True

        for item in scheduled_items:
            # Get all versions of the schedule item
            versions = select(
                sv for sv in ScheduleVersion
                if sv.schedule_item == item and sv.is_active
            )

            for version in versions:
                # Use the version's planned start and end times
                if not earliest_start or version.planned_start_time < earliest_start:
                    earliest_start = version.planned_start_time
                if not latest_end or version.planned_end_time > latest_end:
                    latest_end = version.planned_end_time

        # Determine scheduling status and changeability
        if earliest_start and latest_end:
            if earliest_start <= current_time:
                # Currently running or already started (blue in Gantt chart)
                scheduling_status = "In Progress/Started"
                is_changeable = False
            elif earliest_start > current_time + timedelta(days=1):
                # Scheduled in the future (green or orange in Gantt chart)
                scheduling_status = "Scheduled Future"
                is_changeable = True
            else:
                # Scheduled to start very soon
                scheduling_status = "Scheduled Soon"
                is_changeable = False

        priority_details.append(PriorityDetails(
            part_number=part_number,
            current_priority=current_priority,
            current_status=part_status.status,
            scheduled_start=earliest_start,
            scheduled_end=latest_end,
            is_changeable=is_changeable,
            scheduling_status=scheduling_status
        ))

    return priority_details


@router.put("/update", response_model=PriorityDetails)
@db_session
def update_part_priority(update_request: PriorityUpdateRequest):
    """
    Update part priority with enhanced future scheduling checks
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

        # Check scheduled items and their start times
        scheduled_items = select(
            psi for psi in PlannedScheduleItem
            if psi.order == order
        )

        # Default to changeable
        is_changeable = True
        earliest_start = None
        latest_end = None
        scheduling_status = "Not Scheduled"

        # Analyze scheduled items
        for item in scheduled_items:
            # Get all versions of the schedule item
            versions = select(
                sv for sv in ScheduleVersion
                if sv.schedule_item == item and sv.is_active
            )

            for version in versions:
                # Update earliest and latest times using version's planned times
                if not earliest_start or version.planned_start_time < earliest_start:
                    earliest_start = version.planned_start_time
                if not latest_end or version.planned_end_time > latest_end:
                    latest_end = version.planned_end_time

        # Determine changeability based on scheduling
        if earliest_start and latest_end:
            if earliest_start <= current_time:
                # Currently running or already started (blue in Gantt chart)
                scheduling_status = "In Progress/Started"
                is_changeable = False
            elif earliest_start > current_time + timedelta(days=1):
                # Scheduled in the future (green or orange in Gantt chart)
                scheduling_status = "Scheduled Future"
                is_changeable = True
            else:
                # Scheduled to start very soon
                scheduling_status = "Scheduled Soon"
                is_changeable = False

        # If not changeable, raise an exception
        if not is_changeable:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot change priority. Part is {scheduling_status}."
            )

        # Get current priority
        old_priority = order.project.priority if order.project else 999

        # Update priority
        if not order.project:
            # Create a new project if none exists
            project = Project(
                name=f"Project for {update_request.part_number}",
                priority=update_request.new_priority,
                start_date=datetime.now(),
                end_date=datetime.now(),
                delivery_date=datetime.now()
            )
            order.project = project
        else:
            order.project.priority = update_request.new_priority

        # Commit changes
        commit()

        return PriorityDetails(
            part_number=update_request.part_number,
            current_priority=update_request.new_priority,
            current_status=part_status.status,
            scheduled_start=earliest_start,
            scheduled_end=latest_end,
            is_changeable=True,
            scheduling_status=scheduling_status
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error during priority update: {str(e)}")

# Include this router in your main FastAPI app
# app.include_router(router)