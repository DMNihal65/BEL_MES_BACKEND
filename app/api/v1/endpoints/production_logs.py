from fastapi import APIRouter, HTTPException, Depends, status, Query
from pony.orm import db_session, commit, select, desc
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

# Import your models
from app.models.scheduled import ProductionLog, ScheduleVersion, PlannedScheduleItem
from app.models.user import User
from app.models.master_order import Order, Operation, Machine

# Create router
router = APIRouter(
    prefix="/production",
    tags=["Production"]
)


# Pydantic models for request/response
class ProductionLogBase(BaseModel):
    schedule_version_id: int
    operator_id: int
    start_time: datetime
    end_time: Optional[datetime] = None
    quantity_completed: int
    quantity_rejected: int
    notes: Optional[str] = None


class ProductionLogCreate(ProductionLogBase):
    pass


class ProductionLogUpdate(BaseModel):
    end_time: Optional[datetime] = None
    quantity_completed: Optional[int] = None
    quantity_rejected: Optional[int] = None
    notes: Optional[str] = None

class ProductionLogCreate(BaseModel):
    order_id: int
    operation_id: int
    machine_id: int
    operator_id: int
    start_time: datetime
    end_time: Optional[datetime] = None
    quantity_completed: int
    quantity_rejected: int
    notes: Optional[str] = None


class ProductionLogResponse(BaseModel):
    id: int
    schedule_version_id: int
    operator_id: int
    order_id: int
    operation_id: int
    machine_id: int
    start_time: datetime
    end_time: Optional[datetime] = None
    quantity_completed: int
    quantity_rejected: int
    notes: Optional[str] = None

    class Config:
        orm_mode = True


class ScheduleVersionResponse(BaseModel):
    id: int
    version_number: int
    planned_start_time: datetime
    planned_end_time: datetime
    planned_quantity: int
    completed_quantity: int
    remaining_quantity: int
    is_active: bool

    class Config:
        orm_mode = True


class MachineResponse(BaseModel):
    id: int
    type: str
    make: str
    model: str

    class Config:
        orm_mode = True


class OperationResponse(BaseModel):
    id: int
    operation_number: int
    operation_description: Optional[str] = None
    setup_time: float
    ideal_cycle_time: float

    class Config:
        orm_mode = True


class OrderResponse(BaseModel):
    id: int
    production_order: str
    part_number: str
    part_description: Optional[str] = None
    required_quantity: int

    class Config:
        orm_mode = True


# Get all production logs with pagination
@router.get("/logs", response_model=List[ProductionLogResponse])
@db_session
def get_production_logs(
        skip: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None
):
    query = select(log for log in ProductionLog)

    # Apply date filters if provided
    if date_from:
        query = query.filter(lambda log: log.start_time >= date_from)
    if date_to:
        query = query.filter(lambda log: log.start_time <= date_to)

    # Apply pagination
    logs = query.order_by(desc(ProductionLog.start_time)).limit(limit, offset=skip)[:]

    result = []
    for log in logs:
        planned_item = log.schedule_version.schedule_item
        result.append({
            "id": log.id,
            "schedule_version_id": log.schedule_version.id,
            "operator_id": log.operator.id,
            "order_id": planned_item.order.id,
            "operation_id": planned_item.operation.id,
            "machine_id": planned_item.machine.id,
            "start_time": log.start_time,
            "end_time": log.end_time,
            "quantity_completed": log.quantity_completed,
            "quantity_rejected": log.quantity_rejected,
            "notes": log.notes
        })

    return result


@router.post("/logs", response_model=ProductionLogResponse, status_code=status.HTTP_201_CREATED)
@db_session
def create_production_log(log_data: ProductionLogCreate):
    # Verify that the referenced entities exist
    order = Order.get(id=log_data.order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order with ID {log_data.order_id} not found"
        )

    operation = Operation.get(id=log_data.operation_id)
    if not operation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Operation with ID {log_data.operation_id} not found"
        )

    machine = Machine.get(id=log_data.machine_id)
    if not machine:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Machine with ID {log_data.machine_id} not found"
        )

    operator = User.get(id=log_data.operator_id)
    if not operator:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID {log_data.operator_id} not found"
        )

    # Find the PlannedScheduleItem for this order, operation, and machine
    planned_item = PlannedScheduleItem.get(
        order=order,
        operation=operation,
        machine=machine
    )

    if not planned_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No planned schedule found for order {log_data.order_id}, operation {log_data.operation_id}, and machine {log_data.machine_id}"
        )

    # Find the active schedule version for this planned item
    schedule_version = ScheduleVersion.get(
        schedule_item=planned_item,
        is_active=True
    )

    if not schedule_version:
        # If no active version exists, try to get the latest version
        schedule_versions = list(planned_item.schedule_versions.order_by(lambda v: v.version_number))
        if not schedule_versions:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No schedule version found for the planned schedule item"
            )
        schedule_version = schedule_versions[-1]  # Get the latest version

    # Validate quantity against remaining quantity
    if log_data.quantity_completed > schedule_version.remaining_quantity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Quantity completed ({log_data.quantity_completed}) exceeds remaining quantity ({schedule_version.remaining_quantity})"
        )

    # Create new production log
    new_log = ProductionLog(
        schedule_version=schedule_version,
        operator=operator,
        start_time=log_data.start_time,
        end_time=log_data.end_time,
        quantity_completed=log_data.quantity_completed,
        quantity_rejected=log_data.quantity_rejected,
        notes=log_data.notes
    )

    commit()

    # Update completed quantity in schedule version
    schedule_version.completed_quantity += log_data.quantity_completed
    schedule_version.remaining_quantity = max(0,
                                              schedule_version.planned_quantity - schedule_version.completed_quantity)

    # If remaining quantity is 0, mark as completed
    if schedule_version.remaining_quantity == 0:
        schedule_version.is_active = False

    # Also update the remaining quantity in the PlannedScheduleItem
    planned_item.remaining_quantity = max(0, planned_item.remaining_quantity - log_data.quantity_completed)

    # Update PlannedScheduleItem status if needed
    if planned_item.remaining_quantity == 0:
        planned_item.status = "Completed"

    commit()

    return {
        "id": new_log.id,
        "schedule_version_id": schedule_version.id,
        "operator_id": new_log.operator.id,
        "order_id": planned_item.order.id,
        "operation_id": planned_item.operation.id,
        "machine_id": planned_item.machine.id,
        "start_time": new_log.start_time,
        "end_time": new_log.end_time,
        "quantity_completed": new_log.quantity_completed,
        "quantity_rejected": new_log.quantity_rejected,
        "notes": new_log.notes
    }

# Update production log by ID
@router.put("/logs/{production_id}", response_model=ProductionLogResponse)
@db_session
def update_production_log(production_id: int, log_update: ProductionLogUpdate):
    log = ProductionLog.get(id=production_id)
    if not log:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Production log with ID {production_id} not found"
        )

    # Calculate the difference in completed quantity for updating the schedule version
    original_completed = log.quantity_completed

    # Update fields if provided
    if log_update.end_time is not None:
        log.end_time = log_update.end_time

    if log_update.quantity_completed is not None:
        log.quantity_completed = log_update.quantity_completed

    if log_update.quantity_rejected is not None:
        log.quantity_rejected = log_update.quantity_rejected

    if log_update.notes is not None:
        log.notes = log_update.notes

    # Update schedule version completed quantity if quantity_completed was updated
    if log_update.quantity_completed is not None:
        schedule_version = log.schedule_version
        quantity_difference = log_update.quantity_completed - original_completed

        schedule_version.completed_quantity += quantity_difference
        schedule_version.remaining_quantity = max(0,
                                                  schedule_version.planned_quantity - schedule_version.completed_quantity)

        # If remaining quantity is 0, mark as completed
        if schedule_version.remaining_quantity == 0:
            schedule_version.is_active = False

        # Also update the remaining quantity in the PlannedScheduleItem
        planned_item = schedule_version.schedule_item
        planned_item.remaining_quantity = max(0, planned_item.remaining_quantity - quantity_difference)

        # Update PlannedScheduleItem status if needed
        if planned_item.remaining_quantity == 0:
            planned_item.status = "Completed"

    commit()

    # Get associated PlannedScheduleItem through ScheduleVersion
    planned_item = log.schedule_version.schedule_item

    return {
        "id": log.id,
        "schedule_version_id": log.schedule_version.id,
        "operator_id": log.operator.id,
        "order_id": planned_item.order.id,
        "operation_id": planned_item.operation.id,
        "machine_id": planned_item.machine.id,
        "start_time": log.start_time,
        "end_time": log.end_time,
        "quantity_completed": log.quantity_completed,
        "quantity_rejected": log.quantity_rejected,
        "notes": log.notes
    }


# Get production logs by machine
@router.get("/machine/{machine_id}/logs", response_model=List[ProductionLogResponse])
@db_session
def get_machine_production_logs(
        machine_id: int,
        skip: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None
):
    machine = Machine.get(id=machine_id)
    if not machine:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Machine with ID {machine_id} not found"
        )

    # Find all PlannedScheduleItems for this machine
    planned_items = PlannedScheduleItem.select(lambda item: item.machine.id == machine_id)[:]

    # Get all schedule versions for these planned items
    schedule_version_ids = []
    for item in planned_items:
        for version in item.schedule_versions:
            schedule_version_ids.append(version.id)

    if not schedule_version_ids:
        return []

    # Create query
    query = select(log for log in ProductionLog if log.schedule_version.id in schedule_version_ids)

    # Apply date filters if provided
    if date_from:
        query = query.filter(lambda log: log.start_time >= date_from)
    if date_to:
        query = query.filter(lambda log: log.start_time <= date_to)

    # Apply pagination
    logs = query.order_by(desc(ProductionLog.start_time)).limit(limit, offset=skip)[:]

    result = []
    for log in logs:
        planned_item = log.schedule_version.schedule_item
        result.append({
            "id": log.id,
            "schedule_version_id": log.schedule_version.id,
            "operator_id": log.operator.id,
            "order_id": planned_item.order.id,
            "operation_id": planned_item.operation.id,
            "machine_id": planned_item.machine.id,
            "start_time": log.start_time,
            "end_time": log.end_time,
            "quantity_completed": log.quantity_completed,
            "quantity_rejected": log.quantity_rejected,
            "notes": log.notes
        })

    return result


# Get production logs by operation
@router.get("/operation/{operation_id}/logs", response_model=List[ProductionLogResponse])
@db_session
def get_operation_production_logs(
        operation_id: int,
        skip: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None
):
    operation = Operation.get(id=operation_id)
    if not operation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Operation with ID {operation_id} not found"
        )

    # Find all PlannedScheduleItems for this operation
    planned_items = PlannedScheduleItem.select(lambda item: item.operation.id == operation_id)[:]

    # Get all schedule versions for these planned items
    schedule_version_ids = []
    for item in planned_items:
        for version in item.schedule_versions:
            schedule_version_ids.append(version.id)

    if not schedule_version_ids:
        return []

    # Create query
    query = select(log for log in ProductionLog if log.schedule_version.id in schedule_version_ids)

    # Apply date filters if provided
    if date_from:
        query = query.filter(lambda log: log.start_time >= date_from)
    if date_to:
        query = query.filter(lambda log: log.start_time <= date_to)

    # Apply pagination
    logs = query.order_by(desc(ProductionLog.start_time)).limit(limit, offset=skip)[:]

    result = []
    for log in logs:
        planned_item = log.schedule_version.schedule_item
        result.append({
            "id": log.id,
            "schedule_version_id": log.schedule_version.id,
            "operator_id": log.operator.id,
            "order_id": planned_item.order.id,
            "operation_id": planned_item.operation.id,
            "machine_id": planned_item.machine.id,
            "start_time": log.start_time,
            "end_time": log.end_time,
            "quantity_completed": log.quantity_completed,
            "quantity_rejected": log.quantity_rejected,
            "notes": log.notes
        })

    return result

