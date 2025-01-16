from fastapi import APIRouter, HTTPException, Query, Path
from typing import List, Optional
from pony.orm import db_session, commit
from ..models.master_order import WorkCenter, Machine
from ..schemas.master_order_schemas import (
    WorkCenterCreate, WorkCenterUpdate, WorkCenterResponse,
    MachineCreate, MachineUpdate, MachineResponse
)

router = APIRouter(prefix="/master-order", tags=["Master Order"])

# WorkCenter Routes
@router.post("/workcenters/", response_model=WorkCenterResponse)
@db_session
def create_work_center(work_center: WorkCenterCreate):
    try:
        # Check if work center with same code exists
        existing = WorkCenter.get(code=work_center.code)
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Work center with code {work_center.code} already exists"
            )
        
        db_work_center = WorkCenter(
            code=work_center.code,
            plant_id=work_center.plant_id,
            description=work_center.description,
            operation=work_center.operation
        )
        commit()
        return db_work_center
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/workcenters/", response_model=List[WorkCenterResponse])
@db_session
def get_work_centers(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    plant_id: Optional[str] = None
):
    try:
        query = WorkCenter.select()
        if plant_id:
            query = query.filter(lambda wc: wc.plant_id == plant_id)
        return list(query.offset(skip).limit(limit))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/workcenters/{work_center_id}", response_model=WorkCenterResponse)
@db_session
def get_work_center(
    work_center_id: int = Path(..., description="The ID of the work center to get")
):
    work_center = WorkCenter.get(id=work_center_id)
    if not work_center:
        raise HTTPException(status_code=404, detail="Work center not found")
    return work_center

@router.put("/workcenters/{work_center_id}", response_model=WorkCenterResponse)
@db_session
def update_work_center(
    work_center_id: int,
    work_center: WorkCenterUpdate
):
    db_work_center = WorkCenter.get(id=work_center_id)
    if not db_work_center:
        raise HTTPException(status_code=404, detail="Work center not found")
    
    try:
        # Update only provided fields
        if work_center.code is not None:
            db_work_center.code = work_center.code
        if work_center.plant_id is not None:
            db_work_center.plant_id = work_center.plant_id
        if work_center.description is not None:
            db_work_center.description = work_center.description
        if work_center.operation is not None:
            db_work_center.operation = work_center.operation
        
        commit()
        return db_work_center
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/workcenters/{work_center_id}")
@db_session
def delete_work_center(work_center_id: int):
    work_center = WorkCenter.get(id=work_center_id)
    if not work_center:
        raise HTTPException(status_code=404, detail="Work center not found")
    
    try:
        # Check if work center has associated machines
        if work_center.machines:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete work center with associated machines"
            )
        
        work_center.delete()
        return {"message": "Work center deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Machine Routes
@router.post("/machines/", response_model=MachineResponse)
@db_session
def create_machine(machine: MachineCreate):
    try:
        # Check if work center exists
        work_center = WorkCenter.get(id=machine.work_center_id)
        if not work_center:
            raise HTTPException(
                status_code=404,
                detail="Work center not found"
            )
        
        db_machine = Machine(
            work_center=work_center,
            type=machine.type,
            make=machine.make,
            model=machine.model,
            year_of_installation=machine.year_of_installation,
            cnc_controller=machine.cnc_controller,
            cnc_controller_series=machine.cnc_controller_series,
            remarks=machine.remarks,
            calibration_date=machine.calibration_date,
            last_maintenance_date=machine.last_maintenance_date
        )
        commit()
        return db_machine
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/machines/", response_model=List[MachineResponse])
@db_session
def get_machines(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    work_center_id: Optional[int] = None
):
    try:
        query = Machine.select()
        if work_center_id:
            query = query.filter(lambda m: m.work_center.id == work_center_id)
        return list(query.offset(skip).limit(limit))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/machines/{machine_id}", response_model=MachineResponse)
@db_session
def get_machine(
    machine_id: int = Path(..., description="The ID of the machine to get")
):
    machine = Machine.get(id=machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")
    return machine

@router.put("/machines/{machine_id}", response_model=MachineResponse)
@db_session
def update_machine(
    machine_id: int,
    machine: MachineUpdate
):
    db_machine = Machine.get(id=machine_id)
    if not db_machine:
        raise HTTPException(status_code=404, detail="Machine not found")
    
    try:
        # Update only provided fields
        for field, value in machine.dict(exclude_unset=True).items():
            setattr(db_machine, field, value)
        
        commit()
        return db_machine
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/machines/{machine_id}")
@db_session
def delete_machine(machine_id: int):
    machine = Machine.get(id=machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")
    
    try:
        machine.delete()
        return {"message": "Machine deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) 