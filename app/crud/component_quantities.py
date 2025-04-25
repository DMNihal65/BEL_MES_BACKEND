from typing import Dict, List
from pony.orm import db_session, select
from app.models import Order
from app.schemas.component_quantities import ComponentQuantityIn, ComponentQuantityOut

@db_session
def fetch_component_quantities() -> Dict[str, int]:
    """
    Fetch launched quantities from Order table
    """
    orders = select(o for o in Order)[:]
    return {(o.part_number, o.production_order): o.launched_quantity for o in orders}


@db_session
def insert_component_quantities(quantities: List[ComponentQuantityIn]) -> List[ComponentQuantityOut]:
    """
    Insert or update component quantities in Order table
    """
    results = []
    for qty in quantities:
        component = qty.component
        quantity = qty.quantity
        existing = Order.get(part_number=component)
        if existing:
            existing.launched_quantity = quantity
        else:
            Order(
                part_number=component,
                launched_quantity=quantity,
                required_quantity=quantity
            )
        results.append(ComponentQuantityOut(component=component, quantity=quantity))
    return results