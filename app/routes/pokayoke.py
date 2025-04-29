from fastapi import APIRouter, HTTPException, Depends, Query
from typing import List, Optional
from pony.orm import db_session, select, commit, desc
from datetime import datetime

from ..models.logs import (
    PokaYokeChecklist, 
    PokaYokeChecklistItem, 
    PokaYokeChecklistMachineAssignment,
    PokaYokeCompletedLog,
    PokaYokeItemResponse
)
from ..schemas.pokayoke import (
    ChecklistCreate,
    ChecklistItemCreate,
    ChecklistResponse,
    MachineAssignmentCreate,
    CompletedChecklistSubmit,
    CompletedChecklistResponse,
    ChecklistLogResponse
)
from ..core.security import get_current_user

router = APIRouter(
    prefix="/pokayoke",
    tags=["pokayoke"],
    responses={404: {"description": "Not found"}},
)

# Checklist Template Management

@router.post("/checklists/", response_model=ChecklistResponse)
@db_session
def create_checklist(checklist: ChecklistCreate, current_user=Depends(get_current_user)):
    """Create a new PokaYoke checklist template"""
    new_checklist = PokaYokeChecklist(
        name=checklist.name,
        description=checklist.description,
        created_by=str(current_user.id),
        is_active=True
    )
    commit()
    
    # Add items if provided
    for idx, item in enumerate(checklist.items or []):
        PokaYokeChecklistItem(
            checklist=new_checklist,
            item_text=item.item_text,
            sequence_number=idx + 1,
            item_type=item.item_type,
            is_required=item.is_required,
            expected_value=item.expected_value
        )
    
    return new_checklist.to_dict()

@router.get("/checklists/", response_model=List[ChecklistResponse])
@db_session
def get_checklists(active_only: bool = True):
    """Get all checklist templates"""
    if active_only:
        checklists = select(c for c in PokaYokeChecklist if c.is_active)
    else:
        checklists = select(c for c in PokaYokeChecklist)
    
    return [c.to_dict() for c in checklists]

@router.get("/checklists/{checklist_id}", response_model=ChecklistResponse)
@db_session
def get_checklist(checklist_id: int):
    """Get a specific checklist template with all its items"""
    checklist = PokaYokeChecklist.get(id=checklist_id)
    if not checklist:
        raise HTTPException(status_code=404, detail="Checklist not found")
    
    return checklist.to_dict()

@router.post("/checklists/items/", response_model=dict)
@db_session
def add_checklist_item(item: ChecklistItemCreate, current_user=Depends(get_current_user)):
    """Add an item to an existing checklist"""
    checklist = PokaYokeChecklist.get(id=item.checklist_id)
    if not checklist:
        raise HTTPException(status_code=404, detail="Checklist not found")
    
    # Get the next sequence number
    next_seq = max([i.sequence_number for i in checklist.items], default=0) + 1
    
    new_item = PokaYokeChecklistItem(
        checklist=checklist,
        item_text=item.item_text,
        sequence_number=next_seq,
        item_type=item.item_type,
        is_required=item.is_required,
        expected_value=item.expected_value
    )
    
    return {"message": "Item added successfully", "item_id": new_item.id}

# Machine Assignment

@router.post("/assignments/", response_model=dict)
@db_session
def assign_checklist_to_machine(assignment: MachineAssignmentCreate, current_user=Depends(get_current_user)):
    """Assign a checklist to a machine"""
    checklist = PokaYokeChecklist.get(id=assignment.checklist_id)
    if not checklist:
        raise HTTPException(status_code=404, detail="Checklist not found")
    
    # Deactivate any existing active assignments for this machine and checklist
    existing = select(a for a in PokaYokeChecklistMachineAssignment 
                     if a.machine_id == assignment.machine_id 
                     and a.checklist.id == assignment.checklist_id
                     and a.is_active == True)
    
    for a in existing:
        a.is_active = False
    
    # Create new assignment
    new_assignment = PokaYokeChecklistMachineAssignment(
        checklist=checklist,
        machine_id=assignment.machine_id,
        machine_make=assignment.machine_make,
        assigned_by=str(current_user.id),
        is_active=True
    )
    
    return {"message": "Checklist assigned to machine successfully", "assignment_id": new_assignment.id}

@router.get("/assignments/machine/{machine_id}", response_model=List[dict])
@db_session
def get_machine_checklists(machine_id: int, active_only: bool = True):
    """Get all checklists assigned to a specific machine"""
    if active_only:
        assignments = select(a for a in PokaYokeChecklistMachineAssignment 
                           if a.machine_id == machine_id and a.is_active)
    else:
        assignments = select(a for a in PokaYokeChecklistMachineAssignment 
                           if a.machine_id == machine_id)
    
    return [a.to_dict() for a in assignments]

# Operator Checklist Completion

@router.post("/complete/", response_model=CompletedChecklistResponse)
@db_session
def complete_checklist(submission: CompletedChecklistSubmit, current_user=Depends(get_current_user)):
    """Submit a completed checklist by an operator"""
    checklist = PokaYokeChecklist.get(id=submission.checklist_id)
    if not checklist:
        raise HTTPException(status_code=404, detail="Checklist not found")
    
    # Determine if all items passed
    all_passed = all(item.is_conforming for item in submission.item_responses)
    
    # Create the completed log
    completed_log = PokaYokeCompletedLog(
        checklist=checklist,
        machine_id=submission.machine_id,
        operator_id=str(current_user.id),
        production_order=submission.production_order,
        part_number=submission.part_number,
        completed_at=datetime.now(),
        all_items_passed=all_passed,
        comments=submission.comments
    )
    
    # Add responses for each item
    for resp in submission.item_responses:
        PokaYokeItemResponse(
            completed_log=completed_log,
            item_id=resp.item_id,
            item_text=resp.item_text,  # Store the text for historical records
            response_value=resp.response_value,
            is_conforming=resp.is_conforming
        )
    
    # Make sure all changes are committed to the database to generate IDs
    commit()
    
    return completed_log.to_dict()

# Reporting and Logs

@router.get("/logs/", response_model=List[ChecklistLogResponse])
@db_session
def get_checklist_logs(
    machine_id: Optional[int] = None,
    production_order: Optional[str] = None,
    part_number: Optional[str] = None,
    operator_id: Optional[str] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    """Get logs of completed checklists with various filters for supervisors"""
    query = select(log for log in PokaYokeCompletedLog)
    
    # Apply filters
    if machine_id:
        query = query.filter(lambda log: log.machine_id == machine_id)
    if production_order:
        query = query.filter(lambda log: log.production_order == production_order)
    if part_number:
        query = query.filter(lambda log: log.part_number == part_number)
    if operator_id:
        query = query.filter(lambda log: log.operator_id == operator_id)
    if from_date:
        query = query.filter(lambda log: log.completed_at >= from_date)
    if to_date:
        query = query.filter(lambda log: log.completed_at <= to_date)
    
    # Pagination
    offset = (page - 1) * page_size
    logs = query.order_by(lambda log: desc(log.completed_at)).limit(page_size, offset=offset)
    
    return [log.to_dict() for log in logs]

@router.get("/logs/{log_id}", response_model=ChecklistLogResponse)
@db_session
def get_checklist_log_detail(log_id: int):
    """Get detailed information about a specific completed checklist"""
    log = PokaYokeCompletedLog.get(id=log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Checklist log not found")
    
    return log.to_dict() 