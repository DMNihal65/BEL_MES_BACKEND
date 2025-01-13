from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel

# Import ProjectResponse instead of using raw Project
from orm_class.master_order import Project  # Ensure this import works


# Pydantic schema for response
class UserResponse(BaseModel):
    username: str
    email: str
    role_id: int

    class Config:
        orm_mode = True

class ProjectResponse(BaseModel):
    id: int
    name: str
    priority: int
    start_date: Optional[datetime]
    end_date: Optional[datetime]
    delivery_date: Optional[datetime]

    class Config:
        orm_mode = True

class RawMaterialResponse(BaseModel):
    id: int
    child_part_number: Optional[str]
    description: Optional[str]
    quantity: Optional[float]
    unit_id: Optional[int]
    status_id: Optional[int]

    class Config:
        orm_mode = True

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
    plant_id: Optional[int]
    project: Optional[ProjectResponse]
    raw_materials: List[RawMaterialResponse] = []

    class Config:
        orm_mode = True

class Order(BaseModel):
    id: int
    production_order: str
    sale_order: str
    wbs_element: str
    part_number: str
    part_description: str
    total_operations: int
    required_quantity: float
    launched_quantity: float
    plant_id: int
    project: ProjectResponse  # Use ProjectResponse instead of raw Project


class ResponseModel(BaseModel):
    orders: List[Order]

    class Config:
        orm_mode = True


# Response Model
class OperationResponse(BaseModel):
    id: int
    operation_number: int
    operation_description: str
    setup_time: float
    ideal_cycle_time: float
    work_center: Optional[str] = None

    class Config:
        orm_mode = True

class WorkCenterResponse(BaseModel):
    id: int
    code: str

    class Config:
        orm_mode = True

