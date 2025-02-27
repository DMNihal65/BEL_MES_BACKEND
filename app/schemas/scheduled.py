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


class ScheduledOperation(BaseModel):
    component: str
    description: str
    machine: str
    start_time: datetime
    end_time: datetime
    quantity: str
    production_order: Optional[str]



class ProductionLogResponse(BaseModel):
    """Response model for production log entries"""
    id: int
    operator_id: Optional[int] = None
    operator_name: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    quantity_completed: int = 0
    quantity_rejected: int = 0
    part_number: Optional[str] = None
    operation_description: Optional[str] = None
    machine_name: Optional[str] = None
    notes: Optional[str] = None
    version_number: Optional[int] = None
    scheduled_item_id: Optional[int] = None
    production_order: Optional[str] = None

    class Config:
        from_attributes = True

class ProductionLogsResponse(BaseModel):
    production_logs: List[ProductionLogResponse]
    total_completed: int
    total_rejected: int
    total_logs: int


class CombinedScheduleProductionResponse(BaseModel):
    production_logs: List[ProductionLogResponse]
    scheduled_operations: List[ScheduledOperation] 

class ProductionMetrics(BaseModel):
    oee: float  # Overall Equipment Effectiveness
    availability: float
    performance: float
    quality: float
    total_planned_time: float
    actual_runtime: float
    downtime: float
    ideal_cycle_time: float
    actual_cycle_time: float
    total_pieces: int
    good_pieces: int
    rejected_pieces: int

class MachineStatus(BaseModel):
    machine_id: int
    machine_name: str
    status: str  # running, idle, setup, breakdown, maintenance
    current_order: Optional[str]
    current_operation: Optional[str]
    start_time: Optional[datetime]
    uptime: Optional[float]
    efficiency: Optional[float]

class ProductionKPI(BaseModel):
    target_production: int
    actual_production: int
    efficiency: float
    quality_rate: float
    machine_utilization: float
    cycle_time_variance: float
    setup_time: float
    downtime: float

class ShiftSummary(BaseModel):
    shift: str
    start_time: datetime
    end_time: datetime
    total_production: int
    good_pieces: int
    rejected_pieces: int
    downtime: float
    operators: List[str]
    machines: List[str]
    efficiency: float

class ProductionTrend(BaseModel):
    timestamp: datetime
    production_rate: float
    quality_rate: float = 100.0
    machine_utilization: float = 100.0

class QualityMetrics(BaseModel):
    defect_rate: float
    rework_rate: float
    scrap_rate: float
    first_pass_yield: float
    defect_categories: Dict[str, int]
    quality_issues: List[Dict[str, str]]

class ResourceUtilization(BaseModel):
    machine_id: int
    machine_name: str
    utilization_rate: float
    productive_time: float
    idle_time: float
    setup_time: float
    breakdown_time: float
    maintenance_time: float

class MachineLiveStatus(BaseModel):
    machine_id: int
    machine_name: str
    status: str
    program_number: Optional[str] = ""
    active_program: Optional[str] = ""
    selected_program: Optional[str] = ""
    part_count: Optional[int] = 0
    job_status: Optional[int] = None
    last_updated: datetime
    job_in_progress: Optional[int] = None
    # Order details fields
    production_order: Optional[str] = None
    part_number: Optional[str] = None
    part_description: Optional[str] = None
    required_quantity: Optional[int] = None
    launched_quantity: Optional[int] = None
    operation_number: Optional[int] = None
    operation_description: Optional[str] = None

    class Config:
        from_attributes = True

class StatusChange(BaseModel):
    timestamp: datetime
    status: str
    program: Optional[str]

class PartCount(BaseModel):
    timestamp: datetime
    count: int

class ProgramChange(BaseModel):
    timestamp: datetime
    program: str

class MachineStatusHistory(BaseModel):
    machine_id: int
    machine_name: str
    start_date: datetime
    end_date: datetime
    status_changes: List[StatusChange]
    part_counts: List[PartCount]
    programs: List[ProgramChange]
    hourly_production: Dict[datetime, int]
    status_duration: Dict[str, float]  # Duration in hours

class MachineAnalytics(BaseModel):
    machine_id: int
    machine_name: str
    status_distribution: Dict[str, float]
    production_trends: List[ProductionTrend]
    total_parts: int
    uptime_percentage: float
    average_cycle_time: float

class MachineSummary(BaseModel):
    machine_id: int
    machine_name: str
    total_production: int
    status_distribution: Dict[str, float]

class ProductionSummary(BaseModel):
    start_date: datetime
    end_date: datetime
    total_production: int
    machine_summaries: List[MachineSummary]
    overall_status_distribution: Dict[str, float] 