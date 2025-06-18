from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel

class MachineStatusBase(BaseModel):
    machine_make: str
    machine_id: int
    status_name: str
    description: Optional[str] = None
    available_from: Optional[datetime] = None
    available_to: Optional[datetime] = None

class MachineStatusOut(MachineStatusBase):
    pass

class MachineStatusResponse(BaseModel):
    total_machines: int
    statuses: List[MachineStatusOut]

class UpdateMachineStatusRequest(BaseModel):
    status_id: int
    description: Optional[str] = None
    available_from: Optional[datetime] = None
    available_to: Optional[datetime] = None

# Models for operator updates
class OperatorMachineUpdate(BaseModel):
    description: str
    is_on: bool  # True for machine ON, False for machine OFF
    created_by: Optional[str] = None  # Operator who created the update

class OperatorRawMaterialUpdate(BaseModel):
    description: str
    is_available: bool  # True for available, False for unavailable
    created_by: Optional[str] = None  # Operator who created the update

class StatusOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None

class StatusResponse(BaseModel):
    total_statuses: int
    statuses: List[StatusOut]

# Order info model
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

# Updated notification models with acknowledgment fields
class MachineNotification(BaseModel):
    id: Optional[int] = None  # Notification ID
    machine_id: int
    machine_make: str
    status_name: str
    description: Optional[str]
    updated_at: Optional[datetime]
    created_by: Optional[str] = None
    is_acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None

class RawMaterialNotification(BaseModel):
    id: Optional[int] = None  # Notification ID
    material_id: int  # Added material_id field
    part_number: Optional[str]  # From associated order if available
    status_name: str
    description: Optional[str]
    updated_at: Optional[datetime]
    created_by: Optional[str] = None
    is_acknowledged: bool = False
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None

class MachineNotificationsResponse(BaseModel):
    total_notifications: int
    notifications: List[MachineNotification]

class RawMaterialNotificationsResponse(BaseModel):
    total_notifications: int
    notifications: List[RawMaterialNotification]

# Notification acknowledgment request
class NotificationAcknowledgmentRequest(BaseModel):
    notification_id: int
    user_id: str  # User acknowledging the notification


class IssueIn(BaseModel):
    category: str
    description: str
    machine: int
    reported_by: int