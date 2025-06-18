from pydantic import BaseModel, Field
from typing import Dict, Optional, Any, List
from datetime import datetime


class EMSDataModel(BaseModel):
    """Schema for machine EMS data (both live and historical)"""
    machine_id: int
    timestamp: datetime
    phase_a_voltage: Optional[float] = None
    phase_b_voltage: Optional[float] = None
    phase_c_voltage: Optional[float] = None
    avg_phase_voltage: Optional[float] = None
    line_ab_voltage: Optional[float] = None
    line_bc_voltage: Optional[float] = None
    line_ca_voltage: Optional[float] = None
    avg_line_voltage: Optional[float] = None
    phase_a_current: Optional[float] = None
    phase_b_current: Optional[float] = None
    phase_c_current: Optional[float] = None
    avg_three_phase_current: Optional[float] = None
    power_factor: Optional[float] = None
    frequency: Optional[float] = None
    total_instantaneous_power: Optional[float] = None
    active_energy_delivered: Optional[float] = None
    status: Optional[int] = None

    class Config:
        orm_mode = True


class ShiftwiseEnergyModel(BaseModel):
    """Schema for shiftwise energy data"""
    timestamp: datetime
    first_shift: float
    second_shift: float
    third_shift: float
    total_energy: float
    machine_id: int

    class Config:
        orm_mode = True


class MachineDetailsResponse(BaseModel):

    """Schema for machine details response"""
    machine_id: int
    machine_data: Dict[str, Any]
    status: Optional[int] = None
    timestamp: Optional[datetime] = None

    class Config:
        orm_mode = True


class ShiftwiseEnergyModel(BaseModel):
    """Schema for shiftwise energy data"""
    timestamp: datetime
    first_shift: float
    second_shift: float
    third_shift: float
    total_energy: float
    machine_id: int

    class Config:
        orm_mode = True

class ShiftWiseEnergyRequest(BaseModel):
    machine_id: int
    column_name: str

class ShiftwiseEnergyResponse(BaseModel):
    """Response model for shiftwise energy history data"""
    data: List[Dict[str, Any]]
    timestamp: str