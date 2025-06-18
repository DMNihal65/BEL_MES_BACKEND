from pydantic import BaseModel
from datetime import datetime
from typing import List, Dict, Optional

from app.schemas.operations import WorkCenterMachine


class PartStatusUpdate(BaseModel):
    status: str

class ScheduledOperation(BaseModel):
    component: str
    part_description: str  # Added part description field
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

class MachineInfo(BaseModel):
    id: str
    name: str
    model: str
    type: str

class WorkCenterInfo(BaseModel):
    work_center_code: str
    work_center_name: str
    machines: List[MachineInfo]
    is_schedulable: bool = True


class ScheduleResponse(BaseModel):
    scheduled_operations: List[ScheduledOperation]
    overall_end_time: datetime
    overall_time: str
    daily_production: Dict
    component_status: Dict
    partially_completed: List[str]
    work_centers: List[WorkCenterMachine]


class ProductionLogResponse(BaseModel):
    id: int
    operator_id: int
    start_time: Optional[datetime]  # Made optional
    end_time: Optional[datetime]    # Made optional
    quantity_completed: int
    quantity_rejected: int
    part_number: Optional[str]      # Made optional
    production_order: Optional[str]
    operation_description: Optional[str]  # Made optional
    machine_name: Optional[str]     # Made optional
    notes: Optional[str]
    version_number: Optional[int]   # Made optional

class ProductionLogsResponse(BaseModel):
    production_logs: List[ProductionLogResponse]
    total_completed: int
    total_rejected: int
    total_logs: int


class CombinedScheduleProductionResponse(BaseModel):
    production_logs: List[ProductionLogResponse]
    scheduled_operations: List[ScheduledOperation]

class RescheduleUpdate(BaseModel):
    operation_id: int
    old_version: int
    new_version: int
    completed_qty: int
    remaining_qty: int
    start_time: str
    end_time: str
    machine_id: int
    raw_material_status: str
    operation_number: int
    last_available_operation: int
    part_number: str
    production_order: str

class CombinedScheduleResponse(BaseModel):
    reschedule: List[RescheduleUpdate]  # Changed from updates to reschedule
    total_updates: int
    production_logs: List[ProductionLogResponse]
    scheduled_operations: List[ScheduledOperation]
    overall_end_time: datetime
    overall_time: str
    daily_production: dict
    total_completed: int
    total_rejected: int
    total_logs: int
    work_centers: List[WorkCenterInfo]

class PartProductionTimeline(BaseModel):
    part_number: str
    production_order: str
    completed_total_quantity: int
    operations_count: int
    status: Optional[str]

class PartProductionResponse(BaseModel):
    items: List[PartProductionTimeline]
    total_parts: int

class MachineUtilization(BaseModel):
    """Response model for machine utilization data"""
    machine_id: int
    machine_type: str
    machine_make: str
    machine_model: str
    work_center_name: Optional[str] = None
    work_center_bool: bool
    available_hours: float
    utilized_hours: float
    remaining_hours: float
    utilization_percentage: float


