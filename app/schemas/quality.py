from datetime import datetime
from pydantic import BaseModel, Field, model_validator
from typing import List, Optional
import json

# In quality_schema.py - Update MasterBocBase
class MasterBocBase(BaseModel):
    """Base schema for Master BOC"""
    order_id: int = Field(..., description="Order ID", gt=0)
    document_id: int = Field(..., description="Document ID", gt=0)
    nominal: str = Field(..., description="Nominal value")
    uppertol: float = Field(..., description="Upper tolerance", ge=0)
    lowertol: float = Field(..., description="Lower tolerance", le=0)
    zone: str = Field(..., description="Zone information", min_length=1)
    dimension_type: str = Field(..., description="Type of dimension", min_length=1)
    measured_instrument: str = Field(..., description="Measuring instrument used", min_length=1)
    op_no: int = Field(..., description="Operation number", gt=0)
    bbox: List[float] = Field(
        ...,
        description="Bounding box coordinates [x1, y1, x2, y2, x3, y3, x4, y4]",
        min_items=8,
        max_items=8
    )
    ipid: str = Field(..., description="IP ID", min_length=1)

    @model_validator(mode='after')
    def validate_bbox_values(self) -> 'MasterBocBase':
        """Validate that bbox contains exactly 8 values"""
        if len(self.bbox) != 8:
            raise ValueError("bbox must contain exactly 8 values [x1, y1, x2, y2, x3, y3, x4, y4]")
        return self

class MasterBocCreate(MasterBocBase):
    def to_db_dict(self) -> dict:
        """Convert to database format"""
        data = self.model_dump()
        data['bbox'] = json.dumps(data['bbox'])
        return data

class MasterBocResponse(MasterBocBase):
    id: int
    created_at: datetime

    @classmethod
    def from_orm(cls, db_obj):
        """Convert from ORM object to Pydantic model"""
        data = {
            'id': db_obj.id,
            'order_id': db_obj.order.id,
            'document_id': db_obj.document.id,
            'nominal': db_obj.nominal,
            'uppertol': db_obj.uppertol,
            'lowertol': db_obj.lowertol,
            'zone': db_obj.zone,
            'dimension_type': db_obj.dimension_type,
            'measured_instrument': db_obj.measured_instrument,
            'op_no': db_obj.op_no,
            'bbox': json.loads(db_obj.bbox) if db_obj.bbox else [],
            'ipid': db_obj.ipid,
            'created_at': db_obj.created_at
        }
        return cls(**data)

    class Config:
        from_attributes = True

class StageInspectionBase(BaseModel):
    """Base schema for Stage Inspection"""
    op_id: int = Field(..., description="Operation ID", gt=0)
    nominal_value: str = Field(..., description="Nominal value")
    uppertol: float = Field(..., description="Upper tolerance", ge=0)
    lowertol: float = Field(..., description="Lower tolerance", le=0)
    zone: str = Field(..., description="Zone information", min_length=1)
    dimension_type: str = Field(..., description="Type of dimension", min_length=1)
    measured_1: float = Field(..., description="First measurement")
    measured_2: float = Field(..., description="Second measurement")
    measured_3: float = Field(..., description="Third measurement")
    measured_mean: float = Field(..., description="Mean of measurements")
    measured_instrument: str = Field(..., description="Measuring instrument used")
    used_inst: str = Field(..., description="Instrument used for measurement", min_length=1)
    op_no: int = Field(..., description="Operation number", gt=0)
    order_id: int = Field(..., description="Order ID", gt=0)
    quantity_no: Optional[int] = Field(None, description="Quantity number")

class StageInspectionCreate(StageInspectionBase):
    pass

class StageInspectionResponse(StageInspectionBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

class OrderInfo(BaseModel):
    order_id: int
    production_order: str
    part_number: str

class OperationInfo(BaseModel):
    operation_number: int

class StageInspectionDetail(BaseModel):
    id: int
    op_id: int
    nominal_value: str
    uppertol: float
    lowertol: float
    zone: str
    dimension_type: str
    measured_1: float
    measured_2: float
    measured_3: float
    measured_mean: float
    measured_instrument: str
    used_inst: str
    op_no: int
    order_id: int
    quantity_no: Optional[int] = None
    created_at: datetime


class QualityInspectionResponse(BaseModel):
    order_info: OrderInfo
    inspections: List[StageInspectionDetail]


class OperatorInfo(BaseModel):
    id: int
    username: str
    email: str

class StageInspectionWithOperator(BaseModel):
    id: int
    nominal_value: str
    uppertol: float
    lowertol: float
    zone: str
    dimension_type: str
    measured_1: float
    measured_2: float
    measured_3: float
    measured_mean: float
    measured_instrument: str
    used_inst: str
    quantity_no: Optional[int] = None
    created_at: datetime
    operator: OperatorInfo

class OperationGroup(BaseModel):
    operation_number: int
    inspections: List[StageInspectionWithOperator]

class DetailedQualityInspectionResponse(BaseModel):
    order_id: int
    production_order: str
    part_number: str
    operations: List[int]  # List of all operation numbers
    inspection_data: List[OperationGroup]  # Inspection data grouped by operation

class MasterBocIPIDInfo(BaseModel):
    """Schema for Master BOC IPID information"""
    ipid: str
    zone: str
    dimension_type: str
    nominal: str
    uppertol: float
    lowertol: float
    measured_instrument: str

class IPIDInfo(BaseModel):
    """Information for a specific IPID"""
    zone: str
    dimension_type: str
    nominal: str
    uppertol: float
    lowertol: float
    measured_instrument: str

class OperationIPIDGroup(BaseModel):
    """Group of IPIDs for a specific operation"""
    op_no: int
    ipid: str
    details: IPIDInfo

class OrderIPIDResponse(BaseModel):
    """Response schema for Order IPID information"""
    order_id: int
    production_order: str
    part_number: str
    operations: List[int]  # Added list of all operation numbers
    operation_groups: List[OperationIPIDGroup]

class MeasurementInstrumentsResponse(BaseModel):
    """Response schema for measurement instruments list"""
    instruments: List[str]

class ConnectivityBase(BaseModel):
    """Base schema for Connectivity"""
    inventory_item_id: int = Field(..., description="Inventory Item ID", gt=0)
    instrument: str = Field(..., description="Instrument name", min_length=1)
    uuid: str = Field(..., description="Unique identifier", min_length=1)
    address: str = Field(..., description="Address of the instrument", min_length=1)

class ConnectivityCreate(ConnectivityBase):
    pass

class ConnectivityResponse(ConnectivityBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

class FTPBase(BaseModel):
    """Base schema for FTP"""
    order_id: int = Field(..., description="Order ID", gt=0)
    ipid: str = Field(..., description="IPID from master_boc", min_length=1)
    is_completed: bool = Field(False, description="Whether all stage inspections for this IPID are completed")

class FTPCreate(FTPBase):
    pass

class FTPResponse(FTPBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class StageInspectionWithUserResponse(StageInspectionBase):
    """Response schema for Stage Inspection with User details"""
    id: int
    created_at: datetime
    operator: Optional[OperatorInfo] = None

    class Config:
        from_attributes = True
