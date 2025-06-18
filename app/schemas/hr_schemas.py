from pydantic import BaseModel, EmailStr
from typing import Optional, List

class EmployeeBase(BaseModel):
    name: str
    email: EmailStr
    department: str

class EmployeeCreate(EmployeeBase):
    pass

class EmployeeResponse(EmployeeBase):
    id: int

    class Config:
        from_attributes = True 