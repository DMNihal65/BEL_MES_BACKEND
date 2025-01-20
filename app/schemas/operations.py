from pydantic import BaseModel
from datetime import datetime
from typing import List, Dict, Optional

class OperationOut(BaseModel):
    operation_id: int
    partno: str
    operation: str
    machine_id: int
    machine_name: str
    time: float
    sequence: int
    work_center_id: int

class ScheduledOperation(BaseModel):
    component: str
    description: str
    machine: str
    start_time: datetime
    end_time: datetime
    quantity: str

class ScheduleResponse(BaseModel):
    scheduled_operations: List[ScheduledOperation]
    overall_end_time: datetime
    overall_time: str
    daily_production: Dict[str, Dict[datetime, int]]
    component_status: Dict[str, dict]
    partially_completed: List[str]

class MachineSchedulesOut(BaseModel):
    machine_schedules: Dict[str, List[dict]]