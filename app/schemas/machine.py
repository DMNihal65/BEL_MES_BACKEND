from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class WorkCenterBase(BaseModel):
    code: str = Field(..., description="Unique code for the work center")
    plant_id: str = Field(..., description="Plant ID where work center is located")
    description: Optional[str] = Field(None, description="Description of the work center")
    operation: Optional[str] = Field(None, description="Operation type")

    class Config:
        from_attributes = True

class WorkCenterCreate(WorkCenterBase):
    pass

class WorkCenterUpdate(BaseModel):
    plant_id: Optional[str] = None
    description: Optional[str] = None
    operation: Optional[str] = None

    class Config:
        from_attributes = True

class WorkCenterResponse(WorkCenterBase):
    id: int

    class Config:
        from_attributes = True

class MachineBase(BaseModel):
    type: str = Field(..., description="Type of machine")
    make: str = Field(..., description="Make/manufacturer of machine")
    model: str = Field(..., description="Model number/name")
    year_of_installation: Optional[int] = Field(None, description="Year when machine was installed")
    cnc_controller: Optional[str] = Field(None, description="CNC controller type")
    cnc_controller_series: Optional[str] = Field(None, description="CNC controller series")
    remarks: Optional[str] = Field(None, description="Additional remarks")
    calibration_date: Optional[datetime] = Field(None, description="Last calibration date")
    last_maintenance_date: Optional[datetime] = Field(None, description="Last maintenance date")

    class Config:
        from_attributes = True

class MachineCreate(MachineBase):
    work_center_code: str = Field(..., description="Code of the work center this machine belongs to")

class MachineUpdate(BaseModel):
    work_center_code: Optional[str] = None
    type: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    year_of_installation: Optional[int] = None
    cnc_controller: Optional[str] = None
    cnc_controller_series: Optional[str] = None
    remarks: Optional[str] = None
    calibration_date: Optional[datetime] = None
    last_maintenance_date: Optional[datetime] = None

    class Config:
        from_attributes = True

class MachineResponse(MachineBase):
    id: int
    work_center: WorkCenterResponse

    class Config:
        from_attributes = True

class WorkCenterWithMachines(WorkCenterResponse):
    machines: List[MachineResponse]

    class Config:
        from_attributes = True 