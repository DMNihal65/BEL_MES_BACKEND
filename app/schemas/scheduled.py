from pydantic import BaseModel
from datetime import datetime
from typing import List, Dict, Optional

class PartStatusUpdate(BaseModel):
    status: str

class ScheduledOperation(BaseModel):
    component: str
    description: str
    machine: str
    start_time: datetime
    end_time: datetime
    quantity: str
    production_order: Optional[str]

class DailyProduction(BaseModel):
    date: datetime
    quantity: int

class ComponentStatus(BaseModel):
    scheduled_end_time: Optional[datetime]
    lead_time: Optional[datetime]
    on_time: Optional[bool]
    completed_quantity: int
    total_quantity: int



class ScheduleResponse(BaseModel):
    scheduled_operations: List[ScheduledOperation]
    overall_end_time: datetime
    overall_time: str
    daily_production: Dict
    component_status: Dict
    partially_completed: List[str]


class ProductionLogResponse(BaseModel):
    id: int
    operator_id: int
    start_time: Optional[datetime]  # Made optional
    end_time: Optional[datetime]    # Made optional
    quantity_completed: int
    quantity_rejected: int
    part_number: Optional[str]      # Made optional
    operation_description: Optional[str]  # Made optional
    machine_name: Optional[str]     # Made optional
    notes: Optional[str]
    version_number: Optional[int]   # Made optional

class ProductionLogsResponse(BaseModel):
    production_logs: List[ProductionLogResponse]
    total_completed: int
    total_rejected: int
    total_logs: int