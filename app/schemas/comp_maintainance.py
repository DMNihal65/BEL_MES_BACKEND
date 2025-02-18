from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel

from app.schemas.planning import RawMaterialResponse


class MachineStatusBase(BaseModel):
    machine_make: str
    status_name: str
    description: Optional[str] = None  # Added this field
    available_from: Optional[datetime] = None


class MachineStatusOut(MachineStatusBase):
    pass

class MachineStatusResponse(BaseModel):
    total_machines: int
    statuses: List[MachineStatusOut]

class UpdateMachineStatusRequest(BaseModel):
    status_id: int
    description: Optional[str] = None  # Added this field
    available_from: Optional[datetime] = None


class StatusOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None

class StatusResponse(BaseModel):
    total_statuses: int
    statuses: List[StatusOut]


# Pydantic models
class OrderInfo(BaseModel):
    production_order: str
    part_number: str

class RawMaterialResponse(BaseModel):
    id: int
    child_part_number: str
    description: str | None
    quantity: float
    unit_name: str
    status_name: str
    available_from: datetime | None
    orders: List[OrderInfo]

class RawMaterialsListResponse(BaseModel):
    total_items: int
    raw_materials: List[RawMaterialResponse]

class UpdateRawMaterialRequest(BaseModel):
    description: str | None
    quantity: float
    unit_id: int
    status_id: int
    available_from: datetime | None


class StatusResponse1(BaseModel):
    id: int
    name: str
    description: str | None

class UnitResponse(BaseModel):
    id: int
    name: str

class ReferenceDataResponse(BaseModel):
    statuses: List[StatusResponse1]
    units: List[UnitResponse]