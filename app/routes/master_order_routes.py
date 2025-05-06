from datetime import datetime
from platform import machine
from traceback import format_exc

from fastapi import APIRouter, HTTPException, Query, Path
from typing import List, Optional
from pony.orm import db_session, commit, select, rollback
from ..models.master_order import WorkCenter, Machine, MachineStatus, Status
from ..schemas.master_order_schemas import (
    WorkCenterCreate, WorkCenterUpdate, WorkCenterResponse,
    MachineCreate, MachineUpdate, MachineResponse
)

router = APIRouter(prefix="/api/v1/master-order", tags=["Master Order"])

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
            work_center_name=work_center.work_center_name,
            is_schedulable=work_center.is_schedulable
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
        # Using .page() for pagination
        results = query.page(skip // limit + 1, pagesize=limit)[:]
        return results
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
        if work_center.work_center_name is not None:
            db_work_center.work_center_name = work_center.work_center_name
        if work_center.is_schedulable is not None:
            db_work_center.is_schedulable = work_center.is_schedulable

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

        # Get the "ON" status from status table
        on_status = Status.get(name="ON")
        if not on_status:
            # If "ON" status doesn't exist, create it
            on_status = Status(
                name="ON",
                description="Machine is operational and available"
            )
            commit()  # Commit the status creation

        # Create the machine
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
            calibration_due_date=machine.calibration_due_date,  # Added this field
            last_maintenance_date=machine.last_maintenance_date
        )
        commit()  # Commit the machine creation

        # Create machine status entry
        machine_status = MachineStatus(
            machine=db_machine,
            status=on_status,
            description="Initial status",
            available_from=datetime(2025, 1, 21, 11, 41, 20, 417587)
        )
        commit()  # Commit the machine status creation

        # Return response that matches MachineResponse schema
        return {
            "id": db_machine.id,
            "work_center_id": work_center.id,
            "type": db_machine.type,
            "make": db_machine.make,
            "model": db_machine.model,
            "year_of_installation": db_machine.year_of_installation,
            "cnc_controller": db_machine.cnc_controller,
            "cnc_controller_series": db_machine.cnc_controller_series,
            "remarks": db_machine.remarks,
            "calibration_date": db_machine.calibration_date,
            "calibration_due_date": db_machine.calibration_due_date,
            "last_maintenance_date": db_machine.last_maintenance_date,
            "work_center_boolean": True,  # Add the missing field
            "work_center": {
                "id": work_center.id,
                "code": work_center.code,
                "plant_id": work_center.plant_id,
                "description": work_center.description,
                "operation": work_center.work_center_name,
                "is_schedulable": work_center.is_schedulable  # Adding this required field from WorkCenterResponse
            }
        }

    except Exception as e:
        # Rollback in case of any error
        rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/machines/", response_model=List[MachineResponse])
@db_session
def get_machines(work_center_code: Optional[str] = None):
    """Get all machines, optionally filtered by work center"""
    try:
        # Using Pony ORM query syntax
        if work_center_code:
            work_center = WorkCenter.get(code=work_center_code)
            if not work_center:
                raise HTTPException(
                    status_code=404,
                    detail=f"Work center with code {work_center_code} not found"
                )
            machines = select(m for m in Machine if m.work_center.code == work_center_code)
        else:
            machines = select(m for m in Machine)

        # Convert to list and map the response using Pony ORM attributes
        return [
            MachineResponse(
                id=machine.id,
                work_center_id=machine.work_center.id,
                work_center_boolean=machine.work_center.is_schedulable,
                type=machine.type,
                make=machine.make,
                model=machine.model,
                year_of_installation=machine.year_of_installation,
                cnc_controller=machine.cnc_controller,
                cnc_controller_series=machine.cnc_controller_series,
                remarks=machine.remarks,
                calibration_date=machine.calibration_date,
                calibration_due_date=machine.calibration_due_date,  # Added this field
                last_maintenance_date=machine.last_maintenance_date,
                work_center=WorkCenterResponse(
                    id=machine.work_center.id,
                    code=machine.work_center.code,
                    plant_id=machine.work_center.plant_id,
                    description=machine.work_center.description,
                    operation=machine.work_center.work_center_name,
                    is_schedulable=machine.work_center.is_schedulable
                )
            ) for machine in machines
        ]

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching machines: {str(e)}"
        )

@router.get("/machines/{machine_id}", response_model=MachineResponse)
@db_session
def get_machine(
        machine_id: int = Path(..., description="The ID of the machine to get")
):
    try:
        # Get machine by ID
        machine = Machine.get(id=machine_id)
        if not machine:
            raise HTTPException(status_code=404, detail="Machine not found")

        # Return formatted response using Pydantic model
        return MachineResponse(
            id=machine.id,
            work_center_id=machine.work_center.id,
            work_center_boolean=machine.work_center.is_schedulable,
            type=machine.type,
            make=machine.make,
            model=machine.model,
            year_of_installation=machine.year_of_installation,
            cnc_controller=machine.cnc_controller,
            cnc_controller_series=machine.cnc_controller_series,
            remarks=machine.remarks,
            calibration_date=machine.calibration_date,
            calibration_due_date=machine.calibration_due_date,  # Added this field
            last_maintenance_date=machine.last_maintenance_date,
            work_center=WorkCenterResponse(
                id=machine.work_center.id,
                code=machine.work_center.code,
                plant_id=machine.work_center.plant_id,
                description=machine.work_center.description,
                operation=machine.work_center.work_center_name,
                is_schedulable=machine.work_center.is_schedulable
            )
        )

    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching machine: {str(e)}"
        )

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
        # Update only the fields provided in the request
        for field, value in machine.dict(exclude_unset=True).items():
            setattr(db_machine, field, value)

        commit()

        # Manually construct the full response matching MachineResponse
        return MachineResponse(
            id=db_machine.id,
            work_center_id=db_machine.work_center.id,
            type=db_machine.type,
            make=db_machine.make,
            model=db_machine.model,
            year_of_installation=db_machine.year_of_installation,
            cnc_controller=db_machine.cnc_controller,
            cnc_controller_series=db_machine.cnc_controller_series,
            remarks=db_machine.remarks,
            calibration_date=db_machine.calibration_date,
            calibration_due_date=db_machine.calibration_due_date,
            last_maintenance_date=db_machine.last_maintenance_date,
            work_center_boolean=bool(db_machine.work_center),  # Adjust logic if needed
            work_center=WorkCenterResponse(
                id=db_machine.work_center.id,
                code=db_machine.work_center.code,
                plant_id=db_machine.work_center.plant_id,
                description=db_machine.work_center.description,
                work_center_name=db_machine.work_center.work_center_name,
                is_schedulable=db_machine.work_center.is_schedulable
            )
        )

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


@router.get("/all-machines/", response_model=List[MachineResponse])
@db_session
def get_all_machines():
    """Get all machines without any filters"""
    try:
        # Get all machines using Pony ORM
        all_machines = select(m for m in Machine)

        # Convert to list and map the response using Pony ORM attributes
        return [
            MachineResponse(
                id=machine.id,
                work_center_id=machine.work_center.id,
                work_center_boolean = machine.work_center.is_schedulable,
                type=machine.type,
                make=machine.make,
                model=machine.model,
                year_of_installation=machine.year_of_installation,
                cnc_controller=machine.cnc_controller,
                cnc_controller_series=machine.cnc_controller_series,
                remarks=machine.remarks,
                calibration_date=machine.calibration_date,
                calibration_due_date=machine.calibration_due_date,  # Added this field
                last_maintenance_date=machine.last_maintenance_date,
                work_center=WorkCenterResponse(
                    id=machine.work_center.id,
                    code=machine.work_center.code,
                    plant_id=machine.work_center.plant_id,
                    description=machine.work_center.description,
                    operation=machine.work_center.work_center_name,
                    is_schedulable=machine.work_center.is_schedulable
                )
            ) for machine in all_machines
        ]

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching all machines: {str(e)}"
        )