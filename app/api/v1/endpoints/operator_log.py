from pydantic import BaseModel
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException
from pony.orm import db_session, commit, select
from app.models import ProductionLog, User, Operation
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
    machine_entry.timestamp = datetime.utcnow()

    return {"message": "Machine status updated successfully"}