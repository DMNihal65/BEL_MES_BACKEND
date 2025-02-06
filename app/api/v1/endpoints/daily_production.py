from fastapi import APIRouter, HTTPException, Query
from datetime import datetime, timedelta, date
from pony.orm import db_session, select
from typing import Optional
from app.models import PlannedScheduleItem, Order, ScheduleVersion
from app.schemas.daily_production import DailyProductionResponse, DailyProductionItem

router = APIRouter(prefix="/production", tags=["production"])


async def get_all_production_data(part_number: Optional[str] = None):
    """
    Helper function to get all production data
    """
    with db_session:
        # Base query without any date filtering
        if part_number:
            query = select((si, sv) for si in PlannedScheduleItem
                           for sv in ScheduleVersion
                           if si.order.part_number == str(part_number) and
                           sv.schedule_item == si and
                           sv.is_active)
        else:
            query = select((si, sv) for si in PlannedScheduleItem
                           for sv in ScheduleVersion
                           if sv.schedule_item == si and
                           sv.is_active)

        results = query[:]

        daily_production = []
        total_planned = {}
        total_completed = {}

        for schedule_item, schedule_version in results:
            part_num = schedule_item.order.part_number
            prod_date = schedule_item.initial_start_time.date()

            if part_num not in total_planned:
                total_planned[part_num] = 0
                total_completed[part_num] = 0

            total_planned[part_num] += schedule_item.total_quantity
            total_completed[part_num] += schedule_version.completed_quantity

            daily_production.append(
                DailyProductionItem(
                    part_number=part_num,
                    production_order=schedule_item.order.production_order,
                    date=prod_date,
                    planned_quantity=schedule_item.total_quantity,
                    completed_quantity=schedule_version.completed_quantity,
                    remaining_quantity=schedule_version.remaining_quantity
                )
            )

        # Sort by date and part number
        daily_production.sort(key=lambda x: (x.date, x.part_number))

        return daily_production, total_planned, total_completed


@router.get("/daily/", response_model=DailyProductionResponse)
async def get_daily_production(part_number: Optional[str] = Query(None)):
    """
    Get all production data organized by day
    """
    try:
        daily_production, total_planned, total_completed = await get_all_production_data(part_number)

        return DailyProductionResponse(
            daily_production=daily_production,
            total_planned=total_planned,
            total_completed=total_completed
        )

    except Exception as e:
        print(f"Error in daily production endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/weekly/", response_model=DailyProductionResponse)
async def get_weekly_production(part_number: Optional[str] = Query(None)):
    """Get all production data organized by week"""
    try:
        daily_production, total_planned, total_completed = await get_all_production_data(part_number)

        # Group items by week
        weekly_items = {}
        for item in daily_production:
            # Get the week start date (Monday)
            week_start = item.date - timedelta(days=item.date.weekday())
            week_key = week_start

            if week_key not in weekly_items:
                weekly_items[week_key] = {
                    'planned': {},
                    'completed': {},
                    'remaining': {}
                }

            if item.part_number not in weekly_items[week_key]['planned']:
                weekly_items[week_key]['planned'][item.part_number] = 0
                weekly_items[week_key]['completed'][item.part_number] = 0
                weekly_items[week_key]['remaining'][item.part_number] = 0

            weekly_items[week_key]['planned'][item.part_number] += item.planned_quantity
            weekly_items[week_key]['completed'][item.part_number] += item.completed_quantity
            weekly_items[week_key]['remaining'][item.part_number] += item.remaining_quantity

        # Convert weekly totals to DailyProductionItems
        weekly_production = []
        for week_date, totals in sorted(weekly_items.items()):
            for part_num in totals['planned'].keys():
                weekly_production.append(
                    DailyProductionItem(
                        part_number=part_num,
                        production_order=None,  # Weekly total doesn't have a single order
                        date=week_date,
                        planned_quantity=totals['planned'][part_num],
                        completed_quantity=totals['completed'][part_num],
                        remaining_quantity=totals['remaining'][part_num]
                    )
                )

        return DailyProductionResponse(
            daily_production=weekly_production,
            total_planned=total_planned,
            total_completed=total_completed
        )

    except Exception as e:
        print(f"Error in weekly production: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/monthly/", response_model=DailyProductionResponse)
async def get_monthly_production(part_number: Optional[str] = Query(None)):
    """Get all production data organized by month"""
    try:
        daily_production, total_planned, total_completed = await get_all_production_data(part_number)

        # Group items by month
        monthly_items = {}
        for item in daily_production:
            # Get the first day of the month
            month_key = date(item.date.year, item.date.month, 1)

            if month_key not in monthly_items:
                monthly_items[month_key] = {
                    'planned': {},
                    'completed': {},
                    'remaining': {}
                }

            if item.part_number not in monthly_items[month_key]['planned']:
                monthly_items[month_key]['planned'][item.part_number] = 0
                monthly_items[month_key]['completed'][item.part_number] = 0
                monthly_items[month_key]['remaining'][item.part_number] = 0

            monthly_items[month_key]['planned'][item.part_number] += item.planned_quantity
            monthly_items[month_key]['completed'][item.part_number] += item.completed_quantity
            monthly_items[month_key]['remaining'][item.part_number] += item.remaining_quantity

        # Convert monthly totals to DailyProductionItems
        monthly_production = []
        for month_date, totals in sorted(monthly_items.items()):
            for part_num in totals['planned'].keys():
                monthly_production.append(
                    DailyProductionItem(
                        part_number=part_num,
                        production_order=None,  # Monthly total doesn't have a single order
                        date=month_date,
                        planned_quantity=totals['planned'][part_num],
                        completed_quantity=totals['completed'][part_num],
                        remaining_quantity=totals['remaining'][part_num]
                    )
                )

        return DailyProductionResponse(
            daily_production=monthly_production,
            total_planned=total_planned,
            total_completed=total_completed
        )

    except Exception as e:
        print(f"Error in monthly production: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))