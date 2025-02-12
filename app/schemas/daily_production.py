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

class WeeklyProductionItem(BaseModel):
    part_number: str
    production_order: Optional[str]
    week_start_date: date
    planned_quantity: int
    completed_quantity: int
    remaining_quantity: int
    operation_description: Optional[str] = None

class MonthlyProductionItem(BaseModel):
    part_number: str
    production_order: Optional[str]
    month_start_date: date
    planned_quantity: int
    completed_quantity: int
    remaining_quantity: int
    operation_description: Optional[str] = None

class DailyProductionResponse(BaseModel):
    daily_production: List[DailyProductionItem]
    total_planned: Dict[str, int]
    total_completed: Dict[str, int]

class WeeklyProductionResponse(BaseModel):
    weekly_production: List[WeeklyProductionItem]  # Changed from daily_production to weekly_production
    total_planned: Dict[str, int]
    total_completed: Dict[str, int]

class MonthlyProductionResponse(BaseModel):
    monthly_production: List[MonthlyProductionItem]  # Changed from daily_production to monthly_production
    total_planned: Dict[str, int]
    total_completed: Dict[str, int]