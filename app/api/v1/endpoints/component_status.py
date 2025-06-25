from datetime import datetime, timedelta
from typing import Dict, Optional, List
from fastapi import APIRouter, HTTPException
from pony.orm import db_session

from app.algorithm.scheduling import schedule_operations
from app.crud.component_quantities import fetch_component_quantities
from app.crud.leadtime import fetch_lead_times
from app.crud.operation import fetch_operations
from app.schemas.component_status import ComponentStatus, ComponentStatusResponse

router = APIRouter(prefix="/api/v1", tags=["production"])



def format_time_difference(td: timedelta) -> str:
    """Convert timedelta to a formatted string showing days, hours, minutes"""
    days = td.days
    hours, remainder = divmod(td.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"


@router.get("/component_status/", response_model=ComponentStatusResponse)
@db_session
def get_component_status():
    try:
        early_complete: List[ComponentStatus] = []
        on_time_complete: List[ComponentStatus] = []
        delayed_complete: List[ComponentStatus] = []

        operations_df = fetch_operations()
        if operations_df.empty:
            return ComponentStatusResponse(
                early_complete=[],
                on_time_complete=[],
                delayed_complete=[]
            )

        component_quantities = fetch_component_quantities()
        lead_times = fetch_lead_times()

        scheduling_results = schedule_operations(
            operations_df,
            component_quantities,
            lead_times
        )

        if not scheduling_results or len(scheduling_results) < 5:
            return ComponentStatusResponse(
                early_complete=[],
                on_time_complete=[],
                delayed_complete=[]
            )

        component_status = scheduling_results[4]

        for comp, status in component_status.items():
            scheduled_end_time = status.get('scheduled_end_time')
            if not scheduled_end_time:
                continue

            component = ComponentStatus(
                component=comp,
                scheduled_end_time=scheduled_end_time,
                lead_time=status.get('lead_time'),
                on_time=status.get('on_time', False),
                completed_quantity=status.get('completed_quantity', 0),
                total_quantity=status.get('total_quantity', 0),
                lead_time_provided=status.get('lead_time') is not None,
                delay=None
            )

            if status.get('lead_time'):
                if scheduled_end_time < status['lead_time']:
                    early_complete.append(component)
                elif scheduled_end_time == status['lead_time']:
                    on_time_complete.append(component)
                else:
                    # Calculate delay as time difference
                    time_diff = scheduled_end_time - status['lead_time']
                    component.delay = format_time_difference(time_diff)
                    delayed_complete.append(component)
            else:
                on_time_complete.append(component)

        return ComponentStatusResponse(
            early_complete=early_complete,
            on_time_complete=on_time_complete,
            delayed_complete=delayed_complete
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error processing component status: {str(e)}"
        )