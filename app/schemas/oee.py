# Pydantic models for responses
from datetime import datetime,date
from typing import Optional

from pydantic import BaseModel


class MachineIDResponse(BaseModel):
    machine_id: int

class DateShiftMachineQuery(BaseModel):
    date: date
    shift_id: int
    machine_id: Optional[int] = None

# Add new Pydantic model for daily availability response
class DailyAvailabilityResponse(BaseModel):
    date: datetime
    machine_id: int
    actual_production_minutes: float
    planned_production_minutes: float
    availability_percentage: float
    total_parts: int
    good_parts: int
    bad_parts: int
    performance: float
    quality: float
    oee: float
    availability_loss: float
    performance_loss: float
    quality_loss: float

class ShiftAvailabilityResponse(BaseModel):
    date: datetime
    machine_id: int
    shift_id: int
    actual_production_minutes: float
    planned_production_minutes: float
    off_time: float
    idle_time: float
    theoretical_cycletime: float
    total_parts: int
    good_parts: int
    bad_parts: int
    availability_percentage: float
    performance: float
    quality: float
    oee: float
    availability_loss: float
    performance_loss: float
    quality_loss: float

# Simplified Pydantic model for response
class MachineRawResponse(BaseModel):
    id: int
    machine_id: int
    time_stamp: datetime
    status_name: str
    job_in_progress: str
    program_number: str
    part_count: int

# Add these Pydantic models with your other BaseModel definitions
class WeeklyAvailabilityResponse(BaseModel):
    year: int
    week_number: int
    start_date: datetime
    end_date: datetime
    machine_id: int
    actual_production_minutes: float
    planned_production_minutes: float
    availability_percentage: float
    total_parts: int
    good_parts: int
    bad_parts: int
    performance: float
    quality: float
    oee: float
    availability_loss: float
    performance_loss: float
    quality_loss: float

class MonthlyAvailabilityResponse(BaseModel):
    year: int
    month: int
    start_date: datetime
    end_date: datetime
    machine_id: int
    actual_production_minutes: float
    planned_production_minutes: float
    availability_percentage: float
    total_parts: int
    good_parts: int
    bad_parts: int
    performance: float
    quality: float
    oee: float
    availability_loss: float
    performance_loss: float
    quality_loss: float

# Add this new Pydantic model with your other BaseModel definitions
class AllMachinesStatusResponse(BaseModel):
    machine_id: int
    time_stamp: datetime
    status_name: str
    job_in_progress: str
    program_number: str
    part_count: int