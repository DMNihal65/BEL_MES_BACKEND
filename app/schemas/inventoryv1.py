# schemas.py
from datetime import datetime
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from enum import Enum


class InventoryItemStatus(str, Enum):
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    UNDER_MAINTENANCE = "Under Maintenance"


class CalibrationResult(str, Enum):
    PASS = "Pass"
    FAIL = "Fail"


class InventoryRequestStatus(str, Enum):
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    RETURNED = "Returned"


class TransactionType(str, Enum):
    ISSUE = "Issue"
    RETURN = "Return"
    MAINTENANCE = "Maintenance"


# Inventory Category Schemas
class InventoryCategoryBase(BaseModel):
    name: str
    description: Optional[str] = None


class InventoryCategoryCreate(InventoryCategoryBase):
    created_by: int


class InventoryCategoryResponse(InventoryCategoryBase):
    id: int
    created_at: datetime
    created_by: int

    class Config:
        from_attributes = True


# Inventory SubCategory Schemas
class InventorySubCategoryBase(BaseModel):
    name: str
    description: Optional[str] = None
    dynamic_fields: Dict[str, Any]


class InventorySubCategoryCreate(InventorySubCategoryBase):
    category_id: int
    created_by: int


class InventorySubCategoryResponse(InventorySubCategoryBase):
    id: int
    category_id: int
    created_at: datetime
    created_by: int

    class Config:
        from_attributes = True


# Inventory Item Schemas
class InventoryItemBase(BaseModel):
    item_code: str
    dynamic_data: Dict[str, Any]
    quantity: int
    available_quantity: int
    status: InventoryItemStatus


class InventoryItemCreate(InventoryItemBase):
    subcategory_id: int
    created_by: int


class BulkInventoryItemCreate(BaseModel):
    subcategory_id: int
    created_by: int
    items: List[Dict[str, Any]]  # List of items with their dynamic data and other fields

    class Config:
        json_schema_extra = {
            "example": {
                "subcategory_id": 1,
                "created_by": 1,
                "items": [
                    {
                        "item_code": "EM-001",
                        "dynamic_data": {
                            "diameter": 10.0,
                            "flutes": 4,
                            "length": 75.0
                        },
                        "quantity": 10,
                        "available_quantity": 10,
                        "status": "Active"
                    },
                    {
                        "item_code": "EM-002",
                        "dynamic_data": {
                            "diameter": 12.0,
                            "flutes": 4,
                            "length": 80.0
                        },
                        "quantity": 5,
                        "available_quantity": 5,
                        "status": "Active"
                    }
                ]
            }
        }


class InventoryItemResponse(InventoryItemBase):
    id: int
    subcategory_id: int
    created_at: datetime
    updated_at: datetime
    created_by: int

    class Config:
        from_attributes = True


# Calibration Schedule Schemas
class CalibrationScheduleBase(BaseModel):
    calibration_type: str
    frequency_days: int
    last_calibration: Optional[datetime] = None
    next_calibration: datetime
    remarks: Optional[str] = None


class CalibrationScheduleCreate(CalibrationScheduleBase):
    inventory_item_id: int
    created_by: int


class CalibrationScheduleResponse(CalibrationScheduleBase):
    id: int
    inventory_item_id: int
    created_at: datetime
    updated_at: datetime
    created_by: int

    class Config:
        from_attributes = True


# Calibration History Schemas
class CalibrationHistoryBase(BaseModel):
    calibration_date: datetime
    result: CalibrationResult
    certificate_number: Optional[str] = None
    remarks: Optional[str] = None
    next_due_date: datetime


class CalibrationHistoryCreate(CalibrationHistoryBase):
    calibration_schedule_id: int
    performed_by: int


class CalibrationHistoryResponse(CalibrationHistoryBase):
    id: int
    calibration_schedule_id: int
    performed_by: int
    created_at: datetime

    class Config:
        from_attributes = True


# Inventory Request Schemas
class InventoryRequestBase(BaseModel):
    quantity: int
    purpose: str
    status: InventoryRequestStatus
    expected_return_date: datetime
    actual_return_date: Optional[datetime] = None
    remarks: Optional[str] = None
    inventory_item_code: str


class InventoryRequestCreate(BaseModel):
    inventory_item_id: int
    order_id: int
    operation_id: Optional[int] = None
    quantity: int
    purpose: str
    status: InventoryRequestStatus
    expected_return_date: datetime
    remarks: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "inventory_item_id": 1,
                "order_id": 1,
                "operation_id": 1,
                "quantity": 2,
                "purpose": "Required for milling operation",
                "status": "Pending",
                "expected_return_date": "2024-01-10T00:00:00Z",
                "remarks": "Urgent requirement"
            }
        }


class InventoryRequestResponse(InventoryRequestBase):
    id: int
    inventory_item_id: int
    inventory_item_code: str
    requested_by: int
    requested_by_username: str
    order_id: int
    operation_id: Optional[int]
    approved_by: Optional[int]
    approved_by_username: Optional[str]
    approved_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Inventory Transaction Schemas
class InventoryTransactionBase(BaseModel):
    transaction_type: TransactionType
    quantity: int
    remarks: Optional[str] = None


class InventoryTransactionCreate(InventoryTransactionBase):
    inventory_item_id: int
    performed_by: int
    reference_request_id: Optional[int] = None


class InventoryTransactionResponse(InventoryTransactionBase):
    id: int
    inventory_item_id: int
    performed_by: int
    reference_request_id: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True


class InventoryCategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class InventorySubCategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    dynamic_fields: Optional[Dict[str, Any]] = None
    category_id: Optional[int] = None


class InventoryItemUpdate(BaseModel):
    item_code: Optional[str] = None
    dynamic_data: Optional[Dict[str, Any]] = None
    quantity: Optional[int] = None
    available_quantity: Optional[int] = None
    status: Optional[InventoryItemStatus] = None
    subcategory_id: Optional[int] = None


class CalibrationScheduleUpdate(BaseModel):
    calibration_type: Optional[str] = None
    frequency_days: Optional[int] = None
    last_calibration: Optional[datetime] = None
    next_calibration: Optional[datetime] = None
    remarks: Optional[str] = None


class InventoryRequestUpdate(BaseModel):
    quantity: Optional[int] = None
    purpose: Optional[str] = None
    status: Optional[InventoryRequestStatus] = None
    expected_return_date: Optional[datetime] = None
    actual_return_date: Optional[datetime] = None
    remarks: Optional[str] = None
    approved_by: Optional[int] = None
    approved_at: Optional[datetime] = None


# Analytics Schemas
class StatusCount(BaseModel):
    status: str
    count: int


class TransactionSummary(BaseModel):
    transaction_type: str
    total_quantity: int


class CalibrationDue(BaseModel):
    item_id: int
    item_code: str
    next_calibration: datetime