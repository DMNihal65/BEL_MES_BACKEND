from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime


class PDCBase(BaseModel):
    order_id: int = Field(..., description="Order ID")
    part_number: str = Field(..., description="Part number")
    production_order: str = Field(..., description="Production order")
    pdc_data: datetime = Field(..., description="PDC data timestamp")
    data_source: str = Field(..., description="Data source")
    is_active: bool = Field(True, description="Active status")


class PDCCreate(PDCBase):
    pass


class PDCUpdate(BaseModel):
    order_id: Optional[int] = None
    part_number: Optional[str] = None
    production_order: Optional[str] = None
    pdc_data: Optional[datetime] = None
    data_source: Optional[str] = None
    is_active: Optional[bool] = None


class PDCResponse(PDCBase):
    id: int
    order_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

    # When loading from ORM entities, `order_id` may be a Pony `Order` object.
    # Coerce it to the underlying integer id for the API response.
    @field_validator("order_id", mode="before")
    @classmethod
    def coerce_order_id(cls, value):
        try:
            return getattr(value, "id", value)
        except Exception:
            return value


class PDCListResponse(BaseModel):
    records: List[PDCResponse]
    total_count: int 