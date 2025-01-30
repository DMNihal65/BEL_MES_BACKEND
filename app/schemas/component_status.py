from datetime import datetime, timedelta
from typing import List, Optional, Dict
from fastapi import APIRouter, HTTPException
from pony.orm import db_session, select
from pydantic import BaseModel, Field

class ComponentStatus(BaseModel):
    component: str
    scheduled_end_time: datetime
    lead_time: Optional[datetime] = None
    on_time: bool = Field(default=False)
    completed_quantity: int = Field(default=0)
    total_quantity: int = Field(default=0)
    lead_time_provided: bool = Field(default=False)
    delay: Optional[str] = None  # Changed to string to store formatted time difference

    class Config:
        from_attributes = True

class ComponentStatusResponse(BaseModel):
    early_complete: List[ComponentStatus] = Field(default_factory=list)
    on_time_complete: List[ComponentStatus] = Field(default_factory=list)
    delayed_complete: List[ComponentStatus] = Field(default_factory=list)

    class Config:
        from_attributes = True