from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class RawMaterialIn(BaseModel):
    child_part_number: str
    quantity: float
    unit: str
    status_id: int
    available_from: Optional[datetime] = None

class RawMaterialOut(BaseModel):
    id: int
    child_part_number: str
    quantity: float
    unit: str
    status: str
    available_from: Optional[datetime]

class MachineStatusIn(BaseModel):
    machine_id: int
    status_id: int
    available_from: Optional[datetime] = None

class MachineStatusOut(BaseModel):
    machine_id: int
    machine_name: str
    status: str
    available_from: Optional[datetime]