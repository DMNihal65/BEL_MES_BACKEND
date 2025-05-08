from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List, Any, Dict
from fastapi import APIRouter, HTTPException, Query
from pony.orm import db_session, commit, select, desc
from app.models import ProductionLog, User, Operation, ScheduleVersion, Machine, PlannedScheduleItem
from app.models.production import MachineRawLive


class ProductionLogCreate(BaseModel):
    operator_id: int
    operation_id: int
    machine_id: Optional[int] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    quantity_completed: Optional[int] = None
    quantity_rejected: Optional[int] = None
    notes: Optional[str] = None


class ProductionLogResponse(BaseModel):
    id: int
    operator_id: int
    operation_id: int
    machine_id: Optional[int]
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    quantity_completed: Optional[int]
    quantity_rejected: Optional[int]
    notes: Optional[str]


router = APIRouter(prefix="/api/v1/logs", tags=["operator Logs"])


@router.post("/operator-log", response_model=ProductionLogResponse)
@db_session
def create_production_log(log_data: ProductionLogCreate):
    # Validate operator
    operator = User.get(id=log_data.operator_id)
    if not operator:
        raise HTTPException(status_code=404, detail="Operator not found")

    # Validate operation
    operation = Operation.get(id=log_data.operation_id)
    if not operation:
        raise HTTPException(status_code=404, detail="Operation not found")

    # Create ProductionLog
    new_log = ProductionLog(
        operator=operator,
        operation=operation,
        machine_id=log_data.machine_id,
        start_time=log_data.start_time,
        end_time=log_data.end_time,
        quantity_completed=log_data.quantity_completed,
        quantity_rejected=log_data.quantity_rejected,
        notes=log_data.notes
    )

    # Commit to ensure the ID is generated
    commit()

    return ProductionLogResponse(
        id=new_log.id,
        operator_id=new_log.operator.id,
        operation_id=new_log.operation.id,
        machine_id=new_log.machine_id,
        start_time=new_log.start_time,
        end_time=new_log.end_time,
        quantity_completed=new_log.quantity_completed,
        quantity_rejected=new_log.quantity_rejected,
        notes=new_log.notes
    )




# Pydantic model for request body
class MachineStatusInput(BaseModel):
    machine_id: int
    operation_id: int

@router.post("/machine-raw-live/")
@db_session
def update_machine_status(data: MachineStatusInput):

    # Check if the operation exists
    operation = Operation.get(id=data.operation_id)
    if not operation:
        raise HTTPException(status_code=404, detail="Operation not found")

    # Check if machine exists in MachineRawLive
    machine_entry = MachineRawLive.get(machine_id=data.machine_id)
    if not machine_entry:
        raise HTTPException(status_code=404, detail="Machine not found in MachineRawLive")

    # Update only if machine exists
    machine_entry.actual_job = operation
    # machine_entry.status = 1  # plain text status


    return {"message": "Machine status updated successfully"}


@router.post("/machine-raw-live-deactive/")
@db_session
def update_machine_status(data: MachineStatusInput):

    # Check if machine exists in MachineRawLive
    machine_operation_entry = MachineRawLive.get(machine_id=data.machine_id, actual_job = data.operation_id)
    if not machine_operation_entry:
        raise HTTPException(status_code=404, detail="Machine not found in MachineRawLive")

    # Update only if machine exists
    machine_operation_entry.actual_job = None



    return {"message": "Machine status updated successfully"}


class OperationQuantityResponse(BaseModel):
    operation_id: int
    # operation_name: str
    completed_quantity: int
    remaining_quantity: int
    planned_start_time: datetime
    planned_end_time: datetime
    version_number: int


class MachineScheduleResponse(BaseModel):
    machine_id: int
    # machine_name: str
    data: List[OperationQuantityResponse]


@router.get("/api/machine-schedule-quantities", response_model=MachineScheduleResponse)
@db_session
def get_machine_schedule_quantities(
        machine_id: int,
        start_time: datetime = Query(..., description="Start datetime in ISO format"),
        end_time: datetime = Query(..., description="End datetime in ISO format")
):
    """
    Get completed and remaining quantities for operations scheduled on a specific machine
    within a given time range. Returns data from the latest schedule version for each operation.
    """
    # Check if machine exists
    machine = Machine.get(id=machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail=f"Machine with ID {machine_id} not found")

    # Find all PlannedScheduleItems for the given machine that overlap with the time range
    # An item overlaps if it starts before the end_time and ends after the start_time
    schedule_items = select(item for item in PlannedScheduleItem
                            if item.machine.id == machine_id)

    result_data = []

    for item in schedule_items:
        # Get the latest active schedule version for each item
        latest_version = select(sv for sv in item.schedule_versions
                                if sv.is_active == True and
                                ((sv.planned_start_time <= end_time) and
                                 (sv.planned_end_time >= start_time))
                                ).order_by(desc(ScheduleVersion.version_number)).first()

        if latest_version:
            operation = item.operation

            result_data.append(OperationQuantityResponse(
                operation_id=operation.id,
                # operation_name=operation.name,  # Assuming Operation model has a name field
                completed_quantity=latest_version.completed_quantity,
                remaining_quantity=latest_version.remaining_quantity,
                planned_start_time=latest_version.planned_start_time,
                planned_end_time=latest_version.planned_end_time,
                version_number=latest_version.version_number
            ))

    return MachineScheduleResponse(
        machine_id=machine.id,
        # machine_name=machine.name,  # Assuming Machine model has a name field
        data=result_data
    )


class OperationQuantityDetailResponse(BaseModel):
    """Response model for operation details"""
    operation_id: int
    machine_id: int
    completed_quantity: int
    remaining_quantity: int
    planned_start_time: datetime
    planned_end_time: datetime
    version_number: int
    is_active: bool


@router.get("/api/operation-quantities", response_model=List[OperationQuantityDetailResponse])
@db_session
def get_operation_quantities(
        machine_id: int,
        operation_id: int
):
    """
    Get completed and remaining quantities for a specific operation on a specific machine.
    Returns data from the latest schedule version of all matching schedule items, and
    calculates quantities based on the current time.
    """
    # Check if machine exists
    machine = Machine.get(id=machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail=f"Machine with ID {machine_id} not found")

    # Check if operation exists
    operation = Operation.get(id=operation_id)
    if not operation:
        raise HTTPException(status_code=404, detail=f"Operation with ID {operation_id} not found")

    # Find all PlannedScheduleItems for this machine and operation
    schedule_items = select(item for item in PlannedScheduleItem
                            if item.machine.id == machine_id and item.operation.id == operation_id)

    if not schedule_items:
        raise HTTPException(
            status_code=404,
            detail=f"No schedule found for machine ID {machine_id} and operation ID {operation_id}"
        )

    result_data = []
    current_time = datetime.now()

    for schedule_item in schedule_items:
        # Get the latest active schedule version for each item
        latest_version = select(sv for sv in schedule_item.schedule_versions
                                if sv.is_active == True
                                ).order_by(desc(ScheduleVersion.version_number)).first()

        if latest_version:
            # Calculate quantities based on current time
            if current_time < latest_version.planned_start_time:
                # If current time is before the planned start time, no work has been done
                completed_quantity = 0
                remaining_quantity = latest_version.planned_quantity
            elif current_time >= latest_version.planned_end_time:
                # If current time is after the planned end time, all work is completed
                completed_quantity = latest_version.planned_quantity
                remaining_quantity = 0
            else:
                # If current time is between start and end, calculate in-progress quantities
                elapsed_time = (current_time - latest_version.planned_start_time).total_seconds()
                planned_duration = (latest_version.planned_end_time - latest_version.planned_start_time).total_seconds()
                completion_ratio = elapsed_time / planned_duration
                completed_quantity = int(latest_version.planned_quantity * completion_ratio)
                remaining_quantity = latest_version.planned_quantity - completed_quantity

            result_data.append(OperationQuantityDetailResponse(
                operation_id=operation.id,
                machine_id=machine.id,
                completed_quantity=completed_quantity,
                remaining_quantity=remaining_quantity,
                planned_start_time=latest_version.planned_start_time,
                planned_end_time=latest_version.planned_end_time,
                version_number=latest_version.version_number,
                is_active=latest_version.is_active
            ))

    if not result_data:
        raise HTTPException(
            status_code=404,
            detail=f"No active schedule versions found for machine ID {machine_id} and operation ID {operation_id}"
        )

    return result_data
