from fastapi import APIRouter, HTTPException, Depends
from typing import List, Optional
from pony.orm import db_session, commit, select
from datetime import datetime

from app.models.master_order import Machine, WorkCenter
from app.schemas.machine import (
    MachineCreate, 
    MachineUpdate, 
    MachineResponse,
    WorkCenterCreate,
    WorkCenterUpdate,
    WorkCenterResponse,
    WorkCenterWithMachines
)

router = APIRouter()

def serialize_workcenter(wc: WorkCenter) -> dict:
    """Helper function to serialize WorkCenter"""
    return {
        'id': wc.id,
        'code': wc.code,
        'plant_id': wc.plant_id,
        'description': wc.description,
        'operation': wc.operation
    }

def serialize_machine(machine: Machine) -> dict:
    """Helper function to serialize Machine"""
    return {
        'id': machine.id,
        'type': machine.type,
        'make': machine.make,
        'model': machine.model,
        'year_of_installation': machine.year_of_installation,
        'cnc_controller': machine.cnc_controller,
        'cnc_controller_series': machine.cnc_controller_series,
        'remarks': machine.remarks,
        'calibration_date': machine.calibration_date,
        'last_maintenance_date': machine.last_maintenance_date,
        'work_center': serialize_workcenter(machine.work_center)
    }

# WorkCenter Endpoints
@router.post("/workcenters/", 
    response_model=WorkCenterResponse,
    summary="Create new work center",
    status_code=201)
async def create_workcenter(
    workcenter: WorkCenterCreate
) -> WorkCenterResponse:
    """Create a new work center"""
    try:
        with db_session:
            # Check if workcenter with same code exists
            if WorkCenter.get(code=workcenter.code):
                raise HTTPException(
                    status_code=400,
                    detail=f"Work center with code {workcenter.code} already exists"
                )
            
            new_workcenter = WorkCenter(
                code=workcenter.code,
                plant_id=workcenter.plant_id,
                description=workcenter.description,
                operation=workcenter.operation
            )
            commit()
            
            # Refresh the data after commit
            return WorkCenterResponse.model_validate(new_workcenter.to_dict())
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error creating work center: {str(e)}"
        )

@router.get("/workcenters/", 
    response_model=List[WorkCenterWithMachines],
    summary="Get all work centers with their machines")
async def get_workcenters() -> List[WorkCenterWithMachines]:
    """Get all work centers and their associated machines"""
    try:
        with db_session:
            workcenters = select(wc for wc in WorkCenter)[:]
            result = []
            for wc in workcenters:
                wc_data = serialize_workcenter(wc)
                wc_data['machines'] = [serialize_machine(m) for m in wc.machines]
                result.append(wc_data)
            return [WorkCenterWithMachines.model_validate(wc_data) for wc_data in result]
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching work centers: {str(e)}"
        )

@router.get("/workcenters/{code}", 
    response_model=WorkCenterWithMachines,
    summary="Get work center by code")
async def get_workcenter(code: str) -> WorkCenterWithMachines:
    """Get a specific work center by its code"""
    try:
        with db_session:
            wc = WorkCenter.get(code=code)
            if not wc:
                raise HTTPException(
                    status_code=404,
                    detail=f"Work center with code {code} not found"
                )
            
            wc_data = serialize_workcenter(wc)
            wc_data['machines'] = [serialize_machine(m) for m in wc.machines]
            return WorkCenterWithMachines.model_validate(wc_data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching work center: {str(e)}"
        )

@router.put("/workcenters/{code}", 
    response_model=WorkCenterResponse,
    summary="Update work center")
async def update_workcenter(
    code: str, 
    workcenter: WorkCenterUpdate
) -> WorkCenterResponse:
    """Update a work center's details"""
    try:
        with db_session:
            wc = WorkCenter.get(code=code)
            if not wc:
                raise HTTPException(
                    status_code=404,
                    detail=f"Work center with code {code} not found"
                )
            
            update_data = workcenter.model_dump(exclude_unset=True)
            for field, value in update_data.items():
                setattr(wc, field, value)
            
            commit()
            return WorkCenterResponse.model_validate(wc.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating work center: {str(e)}"
        )

# Machine Endpoints
@router.post("/machines/", 
    response_model=MachineResponse,
    summary="Create new machine",
    status_code=201)
async def create_machine(
    machine: MachineCreate
) -> MachineResponse:
    """Create a new machine and associate it with a work center"""
    try:
        with db_session:
            work_center = WorkCenter.get(code=machine.work_center_code)
            if not work_center:
                raise HTTPException(
                    status_code=404,
                    detail=f"Work center with code {machine.work_center_code} not found"
                )
            
            new_machine = Machine(
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
            
            return MachineResponse.model_validate(serialize_machine(new_machine))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error creating machine: {str(e)}"
        )

@router.get("/machines/", 
    response_model=List[MachineResponse],
    summary="Get all machines")
async def get_machines(
    work_center_code: Optional[str] = None
) -> List[MachineResponse]:
    """Get all machines, optionally filtered by work center"""
    try:
        with db_session:
            if work_center_code:
                wc = WorkCenter.get(code=work_center_code)
                if not wc:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Work center with code {work_center_code} not found"
                    )
                machines = select(m for m in Machine if m.work_center.code == work_center_code)[:]
            else:
                machines = select(m for m in Machine)[:]

            return [MachineResponse.model_validate(serialize_machine(m)) for m in machines]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching machines: {str(e)}"
        )

@router.get("/machines/{machine_id}", 
    response_model=MachineResponse,
    summary="Get machine by ID")
async def get_machine(machine_id: int) -> MachineResponse:
    """Get a specific machine by its ID"""
    try:
        with db_session:
            machine = Machine.get(id=machine_id)
            if not machine:
                raise HTTPException(
                    status_code=404,
                    detail=f"Machine with ID {machine_id} not found"
                )
            return MachineResponse.model_validate(serialize_machine(machine))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching machine: {str(e)}"
        )

@router.put("/machines/{machine_id}", 
    response_model=MachineResponse,
    summary="Update machine")
async def update_machine(
    machine_id: int, 
    machine: MachineUpdate
) -> MachineResponse:
    """Update a machine's details"""
    with db_session:
        db_machine = Machine.get(id=machine_id)
        if not db_machine:
            raise HTTPException(
                status_code=404,
                detail=f"Machine with ID {machine_id} not found"
            )
        
        try:
            # If work center is being updated, verify it exists
            if machine.work_center_code:
                work_center = WorkCenter.get(code=machine.work_center_code)
                if not work_center:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Work center with code {machine.work_center_code} not found"
                    )
                db_machine.work_center = work_center
            
            # Update other fields
            update_data = machine.dict(exclude_unset=True, exclude={'work_center_code'})
            for field, value in update_data.items():
                setattr(db_machine, field, value)
            
            commit()
            return MachineResponse.from_orm(db_machine)
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error updating machine: {str(e)}"
            )

@router.delete("/machines/{machine_id}", 
    summary="Delete machine",
    status_code=204)
async def delete_machine(machine_id: int):
    """Delete a machine"""
    with db_session:
        machine = Machine.get(id=machine_id)
        if not machine:
            raise HTTPException(
                status_code=404,
                detail=f"Machine with ID {machine_id} not found"
            )
        
        try:
            machine.delete()
            commit()
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error deleting machine: {str(e)}"
            ) 