from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime




class ChecklistItemBase(BaseModel):
    item_text: str = Field(..., description="Text of the checklist item")
    item_type: str = Field(..., description="Type of the item (boolean, numerical, text)")
    is_required: bool = Field(True, description="Whether this item is required")
    expected_value: Optional[str] = Field(None, description="Expected value or range if applicable")


class ChecklistItemCreate(ChecklistItemBase):
    checklist_id: int = Field(..., description="ID of the checklist this item belongs to")


class ChecklistCreate(BaseModel):
    name: str = Field(..., description="Name of the checklist")
    description: Optional[str] = Field(None, description="Description of the checklist")
    items: Optional[List[ChecklistItemBase]] = Field(None, description="List of checklist items")


class MachineAssignmentCreate(BaseModel):
    checklist_id: int = Field(..., description="ID of the checklist to assign")
    machine_id: int = Field(..., description="ID of the machine to assign to")
    machine_make: Optional[str] = Field(None, description="Make of the machine")


class ItemResponseSubmit(BaseModel):
    item_id: int = Field(..., description="ID of the checklist item")
    item_text: str = Field(..., description="Text of the checklist item")
    response_value: str = Field(..., description="Response value provided by the operator")
    is_conforming: bool = Field(..., description="Whether the response meets requirements")


class CompletedChecklistSubmit(BaseModel):
    checklist_id: int = Field(..., description="ID of the checklist")
    machine_id: int = Field(..., description="ID of the machine")
    production_order: Optional[str] = Field(None, description="Production order number")
    part_number: Optional[str] = Field(None, description="Part number being produced")
    comments: Optional[str] = Field(None, description="Operator comments")
    item_responses: List[ItemResponseSubmit] = Field(..., description="Responses to individual checklist items")


# Response Schemas

class ChecklistItemResponse(ChecklistItemBase):
    id: int
    sequence_number: int


class ChecklistResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    created_at: datetime
    created_by: str
    is_active: bool
    items: List[Dict[str, Any]]


class ItemResponseData(BaseModel):
    id: int
    item_id: int
    item_text: str
    response_value: str
    is_conforming: bool
    timestamp: datetime


class CompletedChecklistResponse(BaseModel):
    id: int
    checklist_id: int
    checklist_name: str
    machine_id: int
    operator_id: str
    production_order: Optional[str]
    part_number: Optional[str]
    completed_at: datetime
    all_items_passed: bool
    comments: Optional[str]
    responses: List[Dict[str, Any]]


class ChecklistLogResponse(CompletedChecklistResponse):
    pass  # Same schema as CompletedChecklistResponse for now 