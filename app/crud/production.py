# app/crud/production.py

from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pony.orm import db_session, select
from app.models import Order, Operation
from app.schemas.production import ProductionFilterParams, DailyQuantity, PartProduction
from app.crud.operation import fetch_operations
from app.crud.component_quantities import fetch_component_quantities
from app.crud.leadtime import fetch_lead_times
from app.algorithm.scheduling import schedule_operations

def get_part_description(part_number: str) -> Optional[str]:
    """Get part description from Order table."""
    with db_session:
        order = Order.select(lambda o: o.part_number == part_number).first()
        return order.part_description if order else None

def process_daily_production(
    daily_production: Dict,
    filter_params: ProductionFilterParams = None
) -> Tuple[List[PartProduction], int]:
    """
    Process daily production data and apply filters if provided.
    Returns a tuple of (production_data_list, total_parts_produced)
    """
    production_data = []
    total_parts = 0

    for part_number, daily_data in daily_production.items():
        if filter_params and filter_params.part_number and filter_params.part_number != part_number:
            continue

        filtered_quantities = []
        part_total = 0

        for prod_date, quantity in daily_data.items():
            # Apply date filters if provided
            if (filter_params and filter_params.start_date and
                prod_date < filter_params.start_date.date()):
                continue
            if (filter_params and filter_params.end_date and
                prod_date > filter_params.end_date.date()):
                continue

            filtered_quantities.append(DailyQuantity(
                date=prod_date,
                quantity=quantity
            ))
            part_total += quantity

        if filtered_quantities:  # Only include parts with production in the filtered range
            production_data.append(PartProduction(
                part_number=part_number,
                part_description=get_part_description(part_number),
                total_quantity=part_total,
                daily_quantities=sorted(filtered_quantities, key=lambda x: x.date)
            ))
            total_parts += part_total

    return production_data, total_parts

@db_session
def get_daily_production(filter_params: Optional[ProductionFilterParams] = None) -> Tuple[List[PartProduction], int]:
    """
    Get daily production data with optional filtering.
    Returns a tuple of (production_data_list, total_parts_produced)
    """
    try:
        # Fetch required data
        df = fetch_operations()
        component_quantities = fetch_component_quantities()
        lead_times = fetch_lead_times()

        # Get schedule and daily production data
        _, _, _, daily_production, _, _ = schedule_operations(
            df, component_quantities, lead_times
        )

        # Process and filter the data
        production_data, total_parts = process_daily_production(daily_production, filter_params)

        # Sort production data by part number
        production_data.sort(key=lambda x: x.part_number)

        return production_data, total_parts

    except Exception as e:
        print(f"Error in get_daily_production: {str(e)}")
        raise