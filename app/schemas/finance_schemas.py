from pydantic import BaseModel
from datetime import date
from typing import Optional

class SalaryRecordBase(BaseModel):
    employee_id: int
    amount: float
    payment_date: date
    bonus: Optional[float] = None

class SalaryRecordCreate(SalaryRecordBase):
    pass

class SalaryRecordResponse(SalaryRecordBase):
    id: int

    class Config:
        from_attributes = True 