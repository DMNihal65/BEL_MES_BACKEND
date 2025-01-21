# app/schemas/production.py

from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, date


class DailyQuantity(BaseModel):
    date: date
    quantity: int


class PartProductionBase(BaseModel):
    part_number: str
    part_description: Optional[str] = None
    total_quantity: int


class PartProductionCreate(PartProductionBase):
    pass


class PartProduction(PartProductionBase):
    daily_quantities: List[DailyQuantity]

    class Config:
        from_attributes = True


class DailyProductionResponse(BaseModel):
    production_data: List[PartProduction]
    total_parts_produced: int


class ProductionFilterParams(BaseModel):
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    part_number: Optional[str] = None