from datetime import datetime
from typing import Dict, List
from pony.orm import db_session, select
from app.models import Order, Project
from app.schemas.leadtime import LeadTimeIn, LeadTimeOut


@db_session
def fetch_lead_times() -> Dict[str, datetime]:
    """
    Fetch delivery dates from Project table through Order relationship
    """
    orders_with_projects = select((o, o.project) for o in Order)[:]
    return {
        order.part_number: project.delivery_date
        for order, project in orders_with_projects
        if project and project.delivery_date
    }


@db_session
def insert_lead_times(lead_times: List[LeadTimeIn]) -> List[LeadTimeOut]:
    """
    Update project delivery dates for orders
    """
    results = []
    for lt in lead_times:
        part_number = lt.component
        due_date = lt.due_date

        # Get the Order and its Project
        order = Order.get(part_number=part_number)
        if order and order.project:
            order.project.delivery_date = due_date
            results.append(LeadTimeOut(component=part_number, due_date=due_date))

    return results