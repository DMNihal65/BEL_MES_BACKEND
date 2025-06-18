from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


# Pydantic models for request and response
class DowntimeCreate(BaseModel):
    machine_id: int
    category: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[int] = None
    reported_by: Optional[int] = None

class DowntimeAction(BaseModel):
    action_taken: Optional[str] = None

class DowntimeResponse(BaseModel):
    id: int
    machine_id: int
    machine_name: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[int] = None
    reported_by: Optional[int] = None
    open_dt: datetime
    inprogress_dt: Optional[datetime] = None
    closed_dt: Optional[datetime] = None
    action_taken: Optional[str] = None
    status: str  # 'open', 'in_progress', or 'closed'

    class Config:
        orm_mode = True