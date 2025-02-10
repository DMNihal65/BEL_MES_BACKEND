from pydantic import BaseModel
from datetime import date
from typing import List, Dict, Optional

class DailyProductionItem(BaseModel):
    part_number: str
    production_order: Optional[str]
    date: date
    planned_quantity: int
    completed_quantity: int
    remaining_quantity: int
    operation_description: Optional[str] = None

class DailyProductionResponse(BaseModel):
    daily_production: List[DailyProductionItem]
    total_planned: Dict[str, int]  # part_number: total planned quantity
    total_completed: Dict[str, int]  # part_number: total completed quantity
