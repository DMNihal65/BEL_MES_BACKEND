# app/api/endpoints/production.py

from fastapi import APIRouter, HTTPException, Query, Depends
from typing import Optional
from datetime import datetime
from app.schemas.production import (
    DailyProductionResponse,
    ProductionFilterParams,
)
from app.crud.production import get_daily_production

router = APIRouter(prefix="/production", tags=["production"])


@router.get("/daily/", response_model=DailyProductionResponse)
async def get_daily_production_data(
        start_date: Optional[datetime] = Query(None, description="Filter by start date"),
        end_date: Optional[datetime] = Query(None, description="Filter by end date"),
        part_number: Optional[str] = Query(None, description="Filter by part number")
):
    """
    Get daily production quantities for each part number.
    Optionally filter by date range and/or part number.
    """
    try:
        filter_params = ProductionFilterParams(
            start_date=start_date,
            end_date=end_date,
            part_number=part_number
        )

        production_data, total_parts = get_daily_production(filter_params)

        return DailyProductionResponse(
            production_data=production_data,
            total_parts_produced=total_parts
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving daily production data: {str(e)}"
        )


@router.get("/summary/")
async def get_production_summary(
        start_date: Optional[datetime] = Query(None),
        end_date: Optional[datetime] = Query(None)
):
    """
    Get a summary of production data including:
    - Total parts produced
    - Number of unique parts
    - Daily averages
    """
    try:
        filter_params = ProductionFilterParams(
            start_date=start_date,
            end_date=end_date
        )

        production_data, total_parts = get_daily_production(filter_params)

        # Calculate summary statistics
        unique_parts = len(production_data)

        # Calculate date range and daily averages
        all_dates = []
        for part in production_data:
            all_dates.extend([qty.date for qty in part.daily_quantities])

        if all_dates:
            min_date = min(all_dates)
            max_date = max(all_dates)
            days_difference = (max_date - min_date).days + 1
            daily_average = total_parts / days_difference
        else:
            min_date = None
            max_date = None
            daily_average = 0

        return {
            "total_parts": total_parts,
            "unique_parts": unique_parts,
            "start_date": min_date,
            "end_date": max_date,
            "daily_average": round(daily_average, 2)
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving production summary: {str(e)}"
        )