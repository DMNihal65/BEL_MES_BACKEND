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
    quality_rate: float
    machine_utilization: float

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