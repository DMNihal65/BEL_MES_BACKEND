from fastapi import APIRouter, Depends, HTTPException
from pony.orm import db_session, select
from typing import List, Dict
from datetime import datetime

from app.models import ScheduleVersion

# Assuming you have a router set up
router = APIRouter(prefix="/pdc", tags=["pdc"])


@router.get("/part-end-times", response_model=Dict[str, datetime])
@db_session
def get_part_end_times():
    """
    Retrieve the latest end times for each part number from schedule versions.

    Returns:
    - A dictionary with part numbers as keys and their latest schedule end times as values
    """
    try:
        # Query to get the latest planned end time for each unique part number
        part_end_times = select(
            (sv.schedule_item.order.part_number, max(sv.planned_end_time))
            for sv in ScheduleVersion
            if sv.is_active
        )

        # Convert query results to a dictionary
        result = {
            part_number: end_time
            for part_number, end_time in part_end_times
        }

        if not result:
            raise HTTPException(status_code=404, detail="No schedule versions found")

        return result

    except Exception as e:
        # Log the error (you might want to use a proper logging mechanism)
        print(f"Error retrieving part end times: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error while fetching part end times")


# Optional: Endpoint to get detailed schedule version for a specific part number
@router.get("/part/{part_number}/schedule-versions", response_model=List[Dict])
@db_session
def get_part_schedule_versions(part_number: str):
    """
    Retrieve all schedule versions for a specific part number.

    Args:
    - part_number: The part number to retrieve schedule versions for

    Returns:
    - A list of schedule version details
    """
    try:
        # Query to fetch schedule versions for a specific part number
        schedule_versions = select(
            (sv.id,
             sv.version_number,
             sv.planned_start_time,
             sv.planned_end_time,
             sv.planned_quantity,
             sv.completed_quantity,
             sv.is_active)
            for sv in ScheduleVersion
            if sv.schedule_item.order.part_number == part_number
        )

        # Convert to list of dictionaries
        result = [
            {
                "id": sv_id,
                "version_number": version_number,
                "planned_start_time": planned_start_time,
                "planned_end_time": planned_end_time,
                "planned_quantity": planned_quantity,
                "completed_quantity": completed_quantity,
                "is_active": is_active
            }
            for sv_id, version_number, planned_start_time, planned_end_time,
            planned_quantity, completed_quantity, is_active in schedule_versions
        ]

        if not result:
            raise HTTPException(status_code=404, detail=f"No schedule versions found for part number {part_number}")

        return result

    except Exception as e:
        # Log the error (you might want to use a proper logging mechanism)
        print(f"Error retrieving schedule versions for {part_number}: {str(e)}")
        raise HTTPException(status_code=500,
                            detail=f"Internal server error while fetching schedule versions for {part_number}")

