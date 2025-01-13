from pydantic import BaseModel, EmailStr, validator, constr
from typing import Optional, List
from datetime import datetime

from pydantic_schema.respose_models import ProjectResponse, OperationResponse


class UserLogs(BaseModel):
    id: int
    user_id: int
    username: str
    email: str
    login_timestamp: datetime

    # Include other fields as needed

    class Config:
        orm_mode = True


class CreateUser1(BaseModel):
    username: str
    email: EmailStr  # Validates that this is a proper email address
    password: str
    role: str  # Either 'Admin' or 'User'
    passkey: Optional[str] = None  # Optional field for admin registration

    class Config:
        orm_mode = True


class OrderUpdate(BaseModel):
    sale_order: Optional[str] = None
    wbs_element: Optional[str] = None
    part_number: Optional[str] = None
    part_description: Optional[str] = None
    total_operations: Optional[int] = None
    required_quantity: Optional[float] = None
    launched_quantity: Optional[float] = None
    plant_id: Optional[int] = None
    delivery_date: Optional[int] = None  # Epoch timestamp

    @validator('delivery_date')
    def validate_delivery_date(cls, v):
        if v is not None:
            try:
                # Convert epoch to datetime
                return datetime.fromtimestamp(v)
            except ValueError as e:
                raise ValueError(f"Invalid epoch timestamp: {e}")
        return v

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
    project: ProjectResponse
    operations: List[OperationResponse] = []  # Include operation details

    class Config:
        orm_mode = True


class ResponseModel(BaseModel):
    orders: List[Order]

    class Config:
        orm_mode = True

# Request Models
class OperationUpdate(BaseModel):
    operation_number: Optional[int] = None
    operation_description: Optional[str] = None
    setup_time: Optional[float] = None
    ideal_cycle_time: Optional[float] = None
    work_center_code: Optional[str] = None

    class Config:
        orm_mode = True

class OperationCreate(BaseModel):
    order_id: int
    operation_number: int
    operation_description: str
    setup_time: float
    ideal_cycle_time: float
    work_center_code: str

    class Config:
        orm_mode = True

class OrderCreate(BaseModel):
    production_order: constr(min_length=1)  # Required, non-empty string
    sale_order: Optional[str] = None
    wbs_element: Optional[str] = None
    part_number: Optional[str] = None
    part_description: Optional[str] = None
    total_operations: Optional[int] = None
    required_quantity: Optional[float] = None
    launched_quantity: Optional[float] = None
    plant_id: Optional[int] = None
    project_name: constr(min_length=1)  # Required, non-empty string
    delivery_date: Optional[int] = None  # Epoch timestamp

    @validator('delivery_date')
    def validate_delivery_date(cls, v):
        if v is not None:
            try:
                # Convert epoch to datetime
                return datetime.fromtimestamp(v)
            except ValueError as e:
                raise ValueError(f"Invalid epoch timestamp: {e}")
        return v

    @validator('required_quantity', 'launched_quantity')
    def validate_quantities(cls, v):
        if v is not None and v < 0:
            raise ValueError("Quantity cannot be negative")
        return v

    class Config:
        orm_mode = True




class WorkInstructionSection(BaseModel):
    title: str
    instructions: str

# Separate models for creation and updating
class NewMPPCreate(BaseModel):
    part_number: str
    operation_number: int
    fixture_number: str
    ipid_number: str
    datum_x: str
    datum_y: str
    datum_z: str
    work_instructions: List[WorkInstructionSection]

class UpdateMPPSections(BaseModel):
    work_instructions: List[WorkInstructionSection]

class MPPResponse(BaseModel):
    id: int
    order_id: int
    operation_id: int
    document_id: Optional[int]
    fixture_number: str
    ipid_number: str
    datum_x: str
    datum_y: str
    datum_z: str
    work_instructions: dict
    part_number: str  # Added to show part number in response
    operation_number: int  # Added to show operation number in response

    class Config:
        orm_mode = True
