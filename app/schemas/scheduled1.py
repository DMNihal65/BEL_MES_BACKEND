from pydantic import BaseModel
from datetime import datetime
from typing import List, Dict, Optional

from app.schemas.operations import WorkCenterMachine


class PartStatusUpdate(BaseModel):
    status: str

class PartScheduleStartDateResponse(BaseModel):
    start_date: Optional[datetime]


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


# Order Completion Schemas
class OrderCompletionRequest(BaseModel):
    is_completed: bool


class OrderCompletionResponse(BaseModel):
    message: str
    triggered_at: str
    order_id: int


class OrderCompletionStatus(BaseModel):
    order_id: int
    production_order: str
    part_number: str
    project_name: str
    status: str
    progress: float
    completion_date: Optional[str] = None
    message: str


class OrderCompletionRecord(BaseModel):
    order_id: int
    production_order: str
    part_number: str
    project_name: str
    status: str
    progress: float
    completion_date: Optional[str] = None
    message: str


class AllCompletionStatusResponse(BaseModel):
    completion_records: List[OrderCompletionRecord]



# PDC (Production Data Collection) Schemas
class PDCBase(BaseModel):
    """Base PDC schema with common fields"""
    part_number: str
    production_order: str
    pdc_data: datetime
    data_source: str
    is_active: bool = True


class PDCCreate(PDCBase):
    """Schema for creating a new PDC record"""
    order_id: int


class PDCUpdate(BaseModel):
    """Schema for updating a PDC record"""
    part_number: Optional[str] = None
    production_order: Optional[str] = None
    pdc_data: Optional[datetime] = None
    data_source: Optional[str] = None
    is_active: Optional[bool] = None


class PDCResponse(PDCBase):
    """Schema for PDC response"""
    id: int
    order_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PDCListResponse(BaseModel):
    """Schema for list of PDC records"""
    pdc_records: List[PDCResponse]
    total_count: int
    page: int
    page_size: int
    total_pages: int

    class Config:
        from_attributes = True


class PDCFilter(BaseModel):
    """Schema for filtering PDC records"""
    part_number: Optional[str] = None
    production_order: Optional[str] = None
    data_source: Optional[str] = None
    is_active: Optional[bool] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    order_id: Optional[int] = None
    
class ScheduleHistoryBase(BaseModel):
    """Base schema for schedule history"""
    version: int
    is_active: bool = False
    generated_at: datetime


class ScheduleHistoryCreate(ScheduleHistoryBase):
    """Schema for creating a new schedule history entry"""
    pass


class ScheduleHistoryUpdate(BaseModel):
    """Schema for updating a schedule history entry"""
    version: Optional[int] = None
    is_active: Optional[bool] = None
    generated_at: Optional[datetime] = None


class ScheduleHistoryResponse(ScheduleHistoryBase):
    """Schema for schedule history response"""
    id: int

    class Config:
        from_attributes = True


class ScheduleHistoryList(BaseModel):
    """Schema for list of schedule history entries"""
    schedule_histories: List[ScheduleHistoryResponse]
    total_count: int


class PlannedScheduleItemBase(BaseModel):
    """Base schema for planned schedule items"""
    order_id: int
    operation_id: int
    machine_id: int
    initial_start_time: datetime
    initial_end_time: datetime
    total_quantity: int
    remaining_quantity: int
    status: Optional[str] = None
    current_version: Optional[int] = None
    schedule_history_id: Optional[int] = None


class PlannedScheduleItemCreate(PlannedScheduleItemBase):
    """Schema for creating a new planned schedule item"""
    pass


class PlannedScheduleItemUpdate(BaseModel):
    """Schema for updating a planned schedule item"""
    order_id: Optional[int] = None
    operation_id: Optional[int] = None
    machine_id: Optional[int] = None
    initial_start_time: Optional[datetime] = None
    initial_end_time: Optional[datetime] = None
    total_quantity: Optional[int] = None
    remaining_quantity: Optional[int] = None
    status: Optional[str] = None
    current_version: Optional[int] = None
    schedule_history_id: Optional[int] = None


class PlannedScheduleItemResponse(PlannedScheduleItemBase):
    """Schema for planned schedule item response"""
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class PlannedScheduleItemList(BaseModel):
    """Schema for list of planned schedule items"""
    planned_schedule_items: List[PlannedScheduleItemResponse]
    total_count: int
