from fastapi import APIRouter, HTTPException, Query, Body
from typing import List, Optional, Any, Dict
from datetime import datetime
from pony.orm import db_session, select, commit
from pydantic import BaseModel

from app.models import User
# Import just the ScheduleVersion model
from app.models.scheduled import ScheduleVersion, ProductionLog

router = APIRouter(
    prefix="/production",
    tags=["Production logs"]
)


@router.get("/schedule-versions")
@db_session
def get_schedule_versions(
        version_id: Optional[int] = None,
        is_active: Optional[bool] = None
):
    """
    Get all schedule versions with optional filtering.
    Only returns data from the ScheduleVersion table.
    Returns all records without pagination.

    Parameters:
    - version_id: Filter by specific version ID
    - is_active: Filter by active status
    """

    # Start with a base query
    query = select(sv for sv in ScheduleVersion)

    # Apply filters if provided
    if version_id is not None:
        query = query.filter(lambda sv: sv.id == version_id)

    if is_active is not None:
        query = query.filter(lambda sv: sv.is_active == is_active)

    # Get all schedule versions without pagination
    schedule_versions = query.order_by(ScheduleVersion.id.desc())

    # Prepare the response with only ScheduleVersion data
    result = []
    for sv in schedule_versions:
        version_data = {
            "id": sv.id,
            "version_number": sv.version_number,
            "planned_start_time": sv.planned_start_time,
            "planned_end_time": sv.planned_end_time,
            "planned_quantity": sv.planned_quantity,
            "completed_quantity": sv.completed_quantity,
            "remaining_quantity": sv.remaining_quantity,
            "is_active": sv.is_active,
            "created_at": sv.created_at,
            "schedule_item_id": sv.schedule_item.id  # Only include the foreign key ID
        }

        result.append(version_data)

    return result


@router.get("/schedule-versions/{version_id}")
@db_session
def get_schedule_version_by_id(version_id: int):
    """
    Get a specific schedule version by ID.
    Only returns data from the ScheduleVersion table.
    """
    schedule_version = ScheduleVersion.get(id=version_id)

    if not schedule_version:
        raise HTTPException(status_code=404, detail="Schedule version not found")

    # Return only the data from the ScheduleVersion table
    return {
        "id": schedule_version.id,
        "version_number": schedule_version.version_number,
        "planned_start_time": schedule_version.planned_start_time,
        "planned_end_time": schedule_version.planned_end_time,
        "planned_quantity": schedule_version.planned_quantity,
        "completed_quantity": schedule_version.completed_quantity,
        "remaining_quantity": schedule_version.remaining_quantity,
        "is_active": schedule_version.is_active,
        "created_at": schedule_version.created_at,
        "schedule_item_id": schedule_version.schedule_item.id  # Only include the foreign key ID
    }


# Model for request body
class ProductionLogData(BaseModel):
    start_time: datetime
    end_time: Optional[datetime] = None
    quantity_completed: int = 0
    notes: Optional[str] = None


@router.post("/logs", status_code=201)
@db_session
def create_production_log(
        schedule_version_id: int = Query(..., description="ID of the schedule version"),
        operator_id: int = Query(..., description="ID of the operator"),
        log_data: ProductionLogData = Body(...)
):
    """
    Create a new production log entry.

    Query Parameters:
    - schedule_version_id: ID of the ScheduleVersion
    - operator_id: ID of the operator (User)

    Request Body:
    - start_time: Start time of the production
    - end_time: End time of the production (optional)
    - quantity_completed: Number of items completed
    - notes: Additional notes (optional)

    Returns:
    - The created production log data
    """

    # Check if schedule version exists
    schedule_version = ScheduleVersion.get(id=schedule_version_id)
    if not schedule_version:
        raise HTTPException(status_code=404, detail="Schedule version not found")

    # Check if operator exists
    operator = User.get(id=operator_id)
    if not operator:
        raise HTTPException(status_code=404, detail="Operator not found")

    # Create the production log
    try:
        production_log = ProductionLog(
            schedule_version=schedule_version,
            operator=operator,
            start_time=log_data.start_time,
            end_time=log_data.end_time,
            quantity_completed=log_data.quantity_completed,
            quantity_rejected=0,  # Default to 0 as per requirements
            notes=log_data.notes
        )

        # Flush to get the ID
        commit()

        # Update the completed_quantity in the ScheduleVersion
        schedule_version.completed_quantity += log_data.quantity_completed
        schedule_version.remaining_quantity = max(0,
                                                  schedule_version.planned_quantity - schedule_version.completed_quantity)

        # Update the remaining_quantity in the PlannedScheduleItem
        schedule_item = schedule_version.schedule_item
        schedule_item.remaining_quantity = max(0, schedule_item.total_quantity - schedule_version.completed_quantity)

        # Return the created log data
        return {
            "id": production_log.id,
            "schedule_version_id": production_log.schedule_version.id,
            "operator_id": production_log.operator.id,
            "start_time": production_log.start_time,
            "end_time": production_log.end_time,
            "quantity_completed": production_log.quantity_completed,
            "notes": production_log.notes,
            "schedule_version_updated": {
                "completed_quantity": schedule_version.completed_quantity,
                "remaining_quantity": schedule_version.remaining_quantity
            },
            "schedule_item_updated": {
                "remaining_quantity": schedule_item.remaining_quantity
            }
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error creating production log: {str(e)}")