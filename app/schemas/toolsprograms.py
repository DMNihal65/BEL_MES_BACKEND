from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class ProgramBase(BaseModel):
    """Base schema for Program model"""
    program_name: str = Field(..., description="Name of the program")
    program_number: str = Field(..., description="Number of the program")
    version: str = Field(..., description="Version of the program")

    class Config:
        from_attributes = True


class ProgramCreate(ProgramBase):
    """Schema for creating a new program"""
    operation_id: int = Field(..., description="ID of the operation")
    order_id: int = Field(..., description="ID of the order (for validation)")


class ProgramResponse(ProgramBase):
    """Schema for program response"""
    id: int
    operation_id: int
    order_id: int
    update_date: datetime


class ProgramUpdate(BaseModel):
    """Schema for updating a program"""
    program_name: Optional[str] = None
    program_number: Optional[str] = None
    version: Optional[str] = None

    class Config:
        from_attributes = True


class ToolAndFixtureBase(BaseModel):
    """Base schema for ToolAndFixture model"""
    item_type: str = Field(..., description="Type of item (TOOL, FIXTURE, JIG, etc.)")
    item_code: str = Field(..., description="Unique code of the item")
    item_name: str = Field(..., description="Name of the item")
    manufacturer: Optional[str] = Field(default="", description="Manufacturer of the item")
    specifications: Optional[str] = Field(default="", description="Specifications of the item")
    serial_number: Optional[str] = Field(default="", description="Serial number of the item")
    inventory_id: Optional[str] = Field(default="", description="Inventory ID of the item")
    storage_location: Optional[str] = Field(default="", description="Storage location of the item")
    last_calibration_date: Optional[datetime] = Field(default=None, description="Last calibration date")
    next_calibration_date: Optional[datetime] = Field(default=None, description="Next calibration date")
    tool_life: Optional[float] = Field(default=None, description="Tool life in hours or operations")
    tool_life_unit: Optional[str] = Field(default="", description="Unit of tool life (HOURS, OPERATIONS, etc.)")
    status: Optional[str] = Field(default="AVAILABLE", description="Status (AVAILABLE, IN_USE, etc.)")
    remarks: Optional[str] = Field(default="", description="Remarks")
    parameters: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional parameters as JSON")

    class Config:
        from_attributes = True


class ToolAndFixtureCreate(ToolAndFixtureBase):
    """Schema for creating a new tool or fixture"""
    order_id: int = Field(..., description="ID of the order")
    operation_id: Optional[int] = Field(default=None, description="ID of the operation (optional)")


class ToolAndFixtureResponse(ToolAndFixtureBase):
    """Schema for tool/fixture response"""
    id: int
    order_id: int
    operation_id: int
    created_at: datetime
    updated_at: datetime
    usage_history: Optional[Dict[str, Any]] = Field(default_factory=dict)


class ToolAndFixtureUpdate(BaseModel):
    """Schema for updating a tool or fixture"""
    item_type: Optional[str] = Field(default=None, description="Type of item (TOOL, FIXTURE, JIG, etc.)")
    item_code: Optional[str] = Field(default=None, description="Unique code of the item")
    item_name: Optional[str] = Field(default=None, description="Name of the item")
    manufacturer: Optional[str] = Field(default=None, description="Manufacturer of the item")
    specifications: Optional[str] = Field(default=None, description="Specifications of the item")
    serial_number: Optional[str] = Field(default=None, description="Serial number of the item")
    inventory_id: Optional[str] = Field(default=None, description="Inventory ID of the item")
    storage_location: Optional[str] = Field(default=None, description="Storage location of the item")
    last_calibration_date: Optional[datetime] = Field(default=None, description="Last calibration date")
    next_calibration_date: Optional[datetime] = Field(default=None, description="Next calibration date")
    tool_life: Optional[float] = Field(default=None, description="Tool life in hours or operations")
    tool_life_unit: Optional[str] = Field(default=None, description="Unit of tool life (HOURS, OPERATIONS, etc.)")
    status: Optional[str] = Field(default=None, description="Status (AVAILABLE, IN_USE, etc.)")
    remarks: Optional[str] = Field(default=None, description="Remarks")
    parameters: Optional[Dict[str, Any]] = Field(default=None, description="Additional parameters as JSON")

    class Config:
        from_attributes = True


class UsageRecord(BaseModel):
    """Schema for tool/fixture usage records"""
    usage_hours: Optional[float] = None
    operation_count: Optional[int] = None
    status_update: Optional[str] = None


# OrderTool schemas
class OrderToolBase(BaseModel):
    tool_name: str
    tool_number: str
    bel_partnumber: Optional[str] = None
    description: Optional[str] = None
    quantity: int = 1


class OrderToolCreate(OrderToolBase):
    order_id: int
    operation_id: Optional[int] = None


class OrderToolUpdate(BaseModel):
    tool_name: Optional[str] = None
    tool_number: Optional[str] = None
    bel_partnumber: Optional[str] = None
    description: Optional[str] = None
    quantity: Optional[int] = None
    operation_id: Optional[int] = None


class OrderToolResponse(OrderToolBase):
    id: int
    order_id: int
    operation_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True