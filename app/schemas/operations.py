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

from pydantic import BaseModel
from datetime import datetime
from typing import List, Dict, Optional

class MachineInfo(BaseModel):
    id: str
    name: str
    model: str
    type: str

class WorkCenterMachine(BaseModel):
    work_center_code: str
    work_center_name: str
    is_schedulable:bool
    machines: List[MachineInfo]

class ScheduledOperation(BaseModel):
    component: str
    description: str
    machine: str
    start_time: datetime
    end_time: datetime
    quantity: str
    production_order: str

class ScheduleResponse(BaseModel):
    scheduled_operations: List[ScheduledOperation]
    overall_end_time: datetime
    overall_time: str
    daily_production: Dict[str, Dict[datetime, int]]
    component_status: Dict[str, dict]
    partially_completed: List[str]
    work_centers: List[WorkCenterMachine]

# class ScheduledOperation(BaseModel):
#     component: str
#     description: str
#     machine: str
#     start_time: datetime
#     end_time: datetime
#     quantity: str
#     total_quantity: int
#     current_quantity: int
#     today_quantity: int
#     production_order: Optional[str]
#
# class ScheduleResponse(BaseModel):
#     scheduled_operations: List[ScheduledOperation]
#     overall_end_time: datetime
#     overall_time: str
#     daily_production: Dict
#     component_status: Dict
#     partially_completed: List[str]


class MachineSchedulesOut(BaseModel):
    machine_schedules: Dict[str, List[dict]]


