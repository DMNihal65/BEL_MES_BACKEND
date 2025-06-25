from pydantic import BaseModel
from datetime import datetime

class LeadTimeIn(BaseModel):
    component: str
    due_date: datetime

class LeadTimeOut(BaseModel):
    component: str
    due_date: datetime