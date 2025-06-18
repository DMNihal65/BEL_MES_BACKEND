from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

# WorkCenter Schemas
class WorkCenterBase(BaseModel):
    code: str = Field(..., description="Work center code")
    plant_id: str = Field(..., description="Plant ID")
    description: Optional[str] = Field(None, description="Work center description")
    work_center_name: Optional[str] = Field(None, description="Operation type")
    is_schedulable: bool = Field(..., description="Indicates if the work center can be scheduled")


class WorkCenterCreate(WorkCenterBase):
    pass

class WorkCenterUpdate(WorkCenterBase):
    code: Optional[str] = None
    plant_id: Optional[str] = None
    work_center_name: Optional[str] =None

class WorkCenterResponse(WorkCenterBase):
    id: int

    class Config:
        from_attributes = True

# Machine Schemas
class MachineBase(BaseModel):
    work_center_id: int = Field(..., description="ID of the associated work center")
    type: str = Field(..., description="Machine type")
    make: str = Field(..., description="Machine manufacturer")
    model: str = Field(..., description="Machine model")
    year_of_installation: Optional[int] = Field(None, description="Year of installation")
    cnc_controller: Optional[str] = Field(None, description="CNC controller type")
    cnc_controller_series: Optional[str] = Field(None, description="CNC controller series")
    remarks: Optional[str] = Field(None, description="Additional remarks")
    calibration_date: Optional[datetime] = Field(None, description="Last calibration date")
    calibration_due_date: Optional[datetime] = Field(None, description="Next calibration due date")  # Added this field
    last_maintenance_date: Optional[datetime] = Field(None, description="Last maintenance date")

class MachineCreate(MachineBase):
    pass

class MachineUpdate(BaseModel):
    type: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    year_of_installation: Optional[int] = None
    cnc_controller: Optional[str] = None
    cnc_controller_series: Optional[str] = None
    remarks: Optional[str] = None
    calibration_date: Optional[datetime] = None
    calibration_due_date: Optional[datetime] = None  # Added this field
    last_maintenance_date: Optional[datetime] = None

class MachineResponse(MachineBase):
    id: int
    work_center_id: int  # Make sure this field is here
    work_center_boolean: bool
    work_center: WorkCenterResponse

    class Config:
        from_attributes = True


class UpdateSchedulable(BaseModel):
    is_schedulable: bool