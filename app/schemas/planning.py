from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

class OrderUpdateRequest(BaseModel):
    sale_order: str
    wbs_element: str
    part_number: str
    part_description: str
    total_operations: int
    required_quantity: int
    launched_quantity: int
    plant_id: str
    delivery_date: int

class OperationUpdateRequest(BaseModel):
    operation_description: str
    setup_time: float
    ideal_cycle_time: float
    work_center_code: str
    machine_id: int
    production_order: str


# Response Models
class WorkCenterResponse(BaseModel):
    id: int
    code: str

class OperationResponse(BaseModel):
    id: int
    operation_number: int
    operation_description: Optional[str]
    setup_time: float
    ideal_cycle_time: float
    work_center: Optional[str] = None

class ProjectResponse(BaseModel):
    id: int
    name: str
    priority: int
    start_date: datetime
    end_date: datetime

class OrderUpdateResponse(BaseModel):
    id: int
    production_order: str
    sale_order: Optional[str]
    wbs_element: Optional[str]
    part_number: str
    part_description: Optional[str]
    total_operations: int
    required_quantity: float
    launched_quantity: float
    plant_id: str
    delivery_date: datetime
    project: Optional[ProjectResponse]
    operations: List[OperationResponse]

    class Config:
        from_attributes = True


# Request model for creating new order
class CreateOrderRequest(BaseModel):
    production_order: str
    sale_order: str
    wbs_element: str
    part_number: str
    part_description: str
    total_operations: int
    required_quantity: int
    launched_quantity: int
    plant_id: int
    project_name: str


# Request model for creating new operation
class CreateOperationRequest(BaseModel):
    order_id: int
    operation_number: int
    operation_description: str
    setup_time: float
    ideal_cycle_time: float
    work_center_code: str

# Response Models
class UnitResponse(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True

class StatusResponse(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True

class RawMaterialResponse(BaseModel):
    id: int
    child_part_number: str
    description: str
    quantity: float
    unit: UnitResponse
    status: StatusResponse

    class Config:
        from_attributes = True

class ProjectResponse(BaseModel):
    id: int
    name: str
    priority: int
    delivery_date: Optional[datetime]
    start_date: Optional[datetime]
    end_date: Optional[datetime]

    class Config:
        from_attributes = True


class OrderDetailsResponse(BaseModel):
    id: int
    production_order: str
    sale_order: str
    wbs_element: str
    part_number: str
    part_description: str
    total_operations: int
    required_quantity: int
    launched_quantity: int
    plant_id: str
    project: ProjectResponse
    raw_materials: List[RawMaterialResponse]

    class Config:
        from_attributes = True

class OarcUploadResponse(BaseModel):
    message: str
    order_details: OrderDetailsResponse

class GetAllOrders(BaseModel):
    id: int
    production_order: str
    sale_order: Optional[str]
    wbs_element: Optional[str]
    part_number: Optional[str]
    part_description: Optional[str]
    total_operations: Optional[int]
    required_quantity: Optional[float]
    launched_quantity: Optional[float]
    raw_material: Optional[str]
    plant_id: Optional[str]
    project: Optional[ProjectResponse]
    raw_materials: List[RawMaterialResponse] = []

    class Config:
        from_attributes = True

class OrderListResponse(BaseModel):
    orders: List[GetAllOrders]

    class Config:
        from_attributes = True


class SaveDataRequest(BaseModel):
    data: Dict[str, Any]

class ProjectPriorityUpdateRequest(BaseModel):
    priority: int