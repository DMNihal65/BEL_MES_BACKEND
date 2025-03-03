from pydantic import BaseModel
from datetime import datetime
from typing import List, Dict, Optional, Any

class PriorityUpdateRequest(BaseModel):
    part_number: str
    new_priority: int
    force: bool = False  # Optional parameter to force priority change even with running operations

class RunningOperation(BaseModel):
    machine_id: int
    machine_name: str
    operation: str
    start_time: datetime
    end_time: datetime
    percent_complete: float

class DependentOperation(BaseModel):
    part_number: str
    operation: str
    scheduled_start: datetime
    scheduled_end: datetime
    status: str

class PriorityUpdateResponse(BaseModel):
    part_number: str
    old_priority: int
    new_priority: Optional[int]
    priority_changed: bool
    running_operations: List[RunningOperation] = []
    dependent_operations: List[DependentOperation] = []
    message: str
    details: Optional[Dict[str, Any]] = None