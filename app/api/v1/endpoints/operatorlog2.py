from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import Optional, List, Any, Dict
from fastapi import APIRouter, HTTPException, Query
from pony.orm import db_session, commit, select, desc

from app.api.v1.endpoints.dynamic_rescheduling import dynamic_reschedule
from app.models import ProductionLog, User, Operation, ScheduleVersion, Machine, PlannedScheduleItem, Order, WorkCenter
from app.models.production import MachineRawLive
from app.schemas.scheduled1 import CombinedScheduleResponse


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


router = APIRouter(prefix="/api/v1/operatorlogs2", tags=["operatorlogs2"])


def validate_operation_sequence(operation_id: int) -> tuple[bool, str]:
    """
    Validates if the operation can be logged based on sequence and work center schedulability.
    Returns (can_log: bool, reason: str) tuple.
    """
    try:
        # Get the current operation
        current_operation = Operation.get(id=operation_id)
        if not current_operation:
            return False, "Operation not found"

        # Check if work center is schedulable
        work_center = current_operation.work_center
        if not work_center.is_schedulable:
            return False, f"Work center '{work_center.work_center_name or work_center.code}' is not schedulable"

        # Get the order for this operation
        order = current_operation.order
        current_op_number = current_operation.operation_number

        # Get all operations for this order, ordered by operation_number
        all_operations = select(op for op in Operation if op.order == order).order_by(Operation.operation_number)

        # Check if this is the first operation (operation_number = 1 or the lowest)
        first_operation = min(op.operation_number for op in all_operations)
        if current_op_number == first_operation:
            return True, "Valid - First operation"

        # Find all previous operations that should be completed before this one
        previous_operations = select(op for op in Operation
                                     if op.order == order and op.operation_number < current_op_number)

        # Check if all previous operations have production logs with completed quantities
        for prev_op in previous_operations:
            # Check if the previous operation's work center is schedulable
            if not prev_op.work_center.is_schedulable:
                continue  # Skip non-schedulable operations in sequence check

            # Check if there are any production logs for this previous operation
            prev_logs = select(log for log in ProductionLog if log.operation == prev_op)

            if not prev_logs.exists():
                # No logs found for a previous operation
                return False, f"Previous operation {prev_op.operation_number} has no production logs"

            # Check if the previous operation has sufficient completed quantity
            total_completed = sum(log.quantity_completed or 0 for log in prev_logs)

            # Get the required quantity from the order
            required_quantity = order.required_quantity

            # If the previous operation doesn't have enough completed quantity, reject
            if total_completed < required_quantity:
                return False, f"Previous operation {prev_op.operation_number} is incomplete ({total_completed}/{required_quantity})"

        return True, "Valid - All previous operations completed"

    except Exception as e:
        print(f"Error in operation sequence validation: {e}")
        return False, f"Validation error: {str(e)}"


def get_operation_sequence_info(operation_id: int) -> Dict:
    """
    Get information about operation sequence for error messages.
    """
    try:
        current_operation = Operation.get(id=operation_id)
        if not current_operation:
            return {"error": "Operation not found"}

        order = current_operation.order
        current_op_number = current_operation.operation_number
        work_center = current_operation.work_center

        # Get all operations for this order
        all_operations = select(op for op in Operation if op.order == order).order_by(Operation.operation_number)

        # Find incomplete previous operations (only schedulable ones)
        incomplete_operations = []

        for op in all_operations:
            if op.operation_number < current_op_number:
                # Only check schedulable operations
                if not op.work_center.is_schedulable:
                    continue

                logs = select(log for log in ProductionLog if log.operation == op)
                total_completed = sum(log.quantity_completed or 0 for log in logs)

                if total_completed < order.required_quantity:
                    incomplete_operations.append({
                        "operation_number": op.operation_number,
                        "operation_id": op.id,
                        "description": op.operation_description,
                        "work_center": op.work_center.work_center_name or op.work_center.code,
                        "work_center_schedulable": op.work_center.is_schedulable,
                        "completed_quantity": total_completed,
                        "required_quantity": order.required_quantity,
                        "remaining_quantity": order.required_quantity - total_completed
                    })

        return {
            "current_operation_number": current_op_number,
            "current_work_center": work_center.work_center_name or work_center.code,
            "current_work_center_schedulable": work_center.is_schedulable,
            "production_order": order.production_order,
            "part_number": order.part_number,
            "incomplete_previous_operations": incomplete_operations
        }

    except Exception as e:
        return {"error": f"Error getting sequence info: {e}"}


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

    # Validate machine if provided
    if log_data.machine_id:
        machine = Machine.get(id=log_data.machine_id)
        if not machine:
            raise HTTPException(status_code=404, detail="Machine not found")

    # Validate operation sequence and work center schedulability
    can_log, validation_reason = validate_operation_sequence(log_data.operation_id)
    if not can_log:
        sequence_info = get_operation_sequence_info(log_data.operation_id)

        error_detail = {
            "message": f"Cannot create production log: {validation_reason}",
            "current_operation_number": sequence_info.get("current_operation_number"),
            "current_work_center": sequence_info.get("current_work_center"),
            "current_work_center_schedulable": sequence_info.get("current_work_center_schedulable"),
            "production_order": sequence_info.get("production_order"),
            "part_number": sequence_info.get("part_number"),
            "incomplete_previous_operations": sequence_info.get("incomplete_previous_operations", []),
            "validation_reason": validation_reason
        }

        raise HTTPException(
            status_code=400,
            detail=error_detail
        )

    # Validate quantity values
    if log_data.quantity_completed is not None and log_data.quantity_completed < 0:
        raise HTTPException(status_code=400, detail="Quantity completed cannot be negative")

    if log_data.quantity_rejected is not None and log_data.quantity_rejected < 0:
        raise HTTPException(status_code=400, detail="Quantity rejected cannot be negative")

    # Check if quantity_completed exceeds remaining quantity for this operation
    if log_data.quantity_completed is not None and log_data.quantity_completed > 0:
        # Get existing logs for this operation
        existing_logs = select(log for log in ProductionLog if log.operation == operation)
        total_completed_so_far = sum(log.quantity_completed or 0 for log in existing_logs)

        # Calculate remaining quantity needed for this operation
        required_quantity = operation.order.required_quantity
        remaining_quantity = required_quantity - total_completed_so_far

        # Check if the new quantity exceeds what's remaining
        if log_data.quantity_completed > remaining_quantity:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Quantity completed exceeds remaining quantity for this operation",
                    "operation_number": operation.operation_number,
                    "production_order": operation.order.production_order,
                    "part_number": operation.order.part_number,
                    "required_quantity": required_quantity,
                    "already_completed": total_completed_so_far,
                    "remaining_quantity": remaining_quantity,
                    "attempted_quantity": log_data.quantity_completed,
                    "excess_quantity": log_data.quantity_completed - remaining_quantity
                }
            )

    # Validate time sequence if both start and end times are provided
    if log_data.start_time and log_data.end_time:
        if log_data.start_time >= log_data.end_time:
            raise HTTPException(status_code=400, detail="Start time must be before end time")

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


@router.get("/operation-sequence-status/{operation_id}")
@db_session
def get_operation_sequence_status(operation_id: int):
    """
    Get the sequence status for a specific operation.
    Useful for frontend to check before allowing log entry.
    """
    operation = Operation.get(id=operation_id)
    if not operation:
        raise HTTPException(status_code=404, detail="Operation not found")

    can_log, validation_reason = validate_operation_sequence(operation_id)
    sequence_info = get_operation_sequence_info(operation_id)

    return {
        "can_log": can_log,
        "validation_reason": validation_reason,
        "sequence_info": sequence_info
    }


@router.get("/order-operations-status/{order_id}")
@db_session
def get_order_operations_status(order_id: int):
    """
    Get comprehensive status of all operations for a specific order.
    Includes order details, raw materials, project info, and operation statuses.
    """
    order = Order.get(id=order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    operations = select(op for op in Operation if op.order == order).order_by(Operation.operation_number)

    operation_statuses = []
    for op in operations:
        logs = select(log for log in ProductionLog if log.operation == op)
        total_completed = sum(log.quantity_completed or 0 for log in logs)
        total_rejected = sum(log.quantity_rejected or 0 for log in logs)

        can_log, validation_reason = validate_operation_sequence(op.id)

        # Get machine details
        machine_info = None
        if op.machine:
            machine_info = {
                "machine_id": op.machine.id,
                "machine_type": op.machine.type,
                "machine_make": op.machine.make,
                "machine_model": op.machine.model,
                "year_of_installation": op.machine.year_of_installation,
                "cnc_controller": op.machine.cnc_controller,
                "cnc_controller_series": op.machine.cnc_controller_series,
                "calibration_date": op.machine.calibration_date,
                "calibration_due_date": op.machine.calibration_due_date,
                "last_maintenance_date": op.machine.last_maintenance_date
            }

        operation_statuses.append({
            "operation_id": op.id,
            "operation_number": op.operation_number,
            "description": op.operation_description,
            "work_center": {
                "id": op.work_center.id,
                "code": op.work_center.code,
                "name": op.work_center.work_center_name,
                "description": op.work_center.description,
                "is_schedulable": op.work_center.is_schedulable,
                "plant_id": op.work_center.plant_id
            },
            "machine": machine_info,
            "setup_time": float(op.setup_time),
            "ideal_cycle_time": float(op.ideal_cycle_time),
            "operation_time": float(op.ideal_cycle_time) * order.required_quantity,
            "can_log": can_log,
            "validation_reason": validation_reason,
            "completed_quantity": total_completed,
            "rejected_quantity": total_rejected,
            "required_quantity": order.required_quantity,
            "remaining_quantity": max(0, order.required_quantity - total_completed),
            "is_complete": total_completed >= order.required_quantity,
            "completion_percentage": (
                        total_completed / order.required_quantity * 100) if order.required_quantity > 0 else 0
        })

    # Get raw material information
    raw_material_info = None
    if order.raw_material:
        raw_material_info = {
            "id": order.raw_material.id,
            "material_name": getattr(order.raw_material, 'material_name', None),
            "material_code": getattr(order.raw_material, 'material_code', None),
            "specification": getattr(order.raw_material, 'specification', None),
            "grade": getattr(order.raw_material, 'grade', None),
            "supplier": getattr(order.raw_material, 'supplier', None),
            "unit_of_measurement": getattr(order.raw_material, 'unit_of_measurement', None),
            "cost_per_unit": getattr(order.raw_material, 'cost_per_unit', None)
        }

    # Get project information
    project_info = None
    if order.project:
        project_info = {
            "id": order.project.id,
            "name": order.project.name,
            "priority": order.project.priority,
            "start_date": order.project.start_date,
            "end_date": order.project.end_date,
            "delivery_date": order.project.delivery_date
        }

    # Calculate overall order progress
    total_operations = len(operation_statuses)
    completed_operations = sum(1 for op in operation_statuses if op["is_complete"])
    overall_completion_percentage = (completed_operations / total_operations * 100) if total_operations > 0 else 0

    # Get order tools information
    order_tools = []
    for tool in order.order_tools:
        order_tools.append({
            "id": tool.id,
            "tool_name": tool.tool_name,
            "tool_number": tool.tool_number,
            "bel_partnumber": tool.bel_partnumber,
            "description": tool.description,
            "quantity": tool.quantity,
            "operation_id": tool.operation.id if tool.operation else None,
            "operation_number": tool.operation.operation_number if tool.operation else None
        })

    return {
        "order_info": {
            "id": order.id,
            "production_order": order.production_order,
            "sale_order": order.sale_order,
            "wbs_element": order.wbs_element,
            "part_number": order.part_number,
            "part_description": order.part_description,
            "total_operations": order.total_operations,
            "required_quantity": order.required_quantity,
            "launched_quantity": order.launched_quantity,
            "plant_id": order.plant_id
        },
        "raw_material": raw_material_info,
        "project": project_info,
        "order_tools": order_tools,
        "operations": operation_statuses,
        "summary": {
            "total_operations": total_operations,
            "completed_operations": completed_operations,
            "remaining_operations": total_operations - completed_operations,
            "overall_completion_percentage": round(overall_completion_percentage, 2),
            "total_completed_quantity": sum(op["completed_quantity"] for op in operation_statuses),
            "total_rejected_quantity": sum(op["rejected_quantity"] for op in operation_statuses),
            "can_start_production": operation_statuses[0]["can_log"] if operation_statuses else False,
            "next_operation_ready": next((op for op in operation_statuses if op["can_log"] and not op["is_complete"]),
                                         None)
        }
    }

@router.get("/operation-sequence-status/{operation_id}")
@db_session
def get_operation_sequence_status(operation_id: int):
    """
    Get the sequence status for a specific operation.
    Useful for frontend to check before allowing log entry.
    """
    operation = Operation.get(id=operation_id)
    if not operation:
        raise HTTPException(status_code=404, detail="Operation not found")

    can_log, validation_reason = validate_operation_sequence(operation_id)
    sequence_info = get_operation_sequence_info(operation_id)

    return {
        "can_log": can_log,
        "validation_reason": validation_reason,
        "sequence_info": sequence_info
    }


# @router.get("/production-order-operations-status/{production_order}")
# @db_session
# def get_production_order_operations_status(production_order: str):
#     """
#     Get comprehensive status of all operations for a specific production order.
#     Includes raw materials, project details, and operation status.
#     """
#     order = Order.get(production_order=production_order)
#     if not order:
#         raise HTTPException(status_code=404, detail="Production order not found")
#
#     operations = select(op for op in Operation if op.order == order).order_by(Operation.operation_number)
#
#     operation_statuses = []
#     for op in operations:
#         logs = select(log for log in ProductionLog if log.operation == op)
#         total_completed = sum(log.quantity_completed or 0 for log in logs)
#         total_rejected = sum(log.quantity_rejected or 0 for log in logs)
#
#         can_log, validation_reason = validate_operation_sequence(op.id)
#
#         # Get machine details
#         machine_info = None
#         if op.machine:
#             machine_info = {
#                 "machine_id": op.machine.id,
#                 "machine_type": op.machine.type,
#                 "machine_make": op.machine.make,
#                 "machine_model": op.machine.model,
#                 "year_of_installation": op.machine.year_of_installation,
#                 "cnc_controller": op.machine.cnc_controller,
#                 "cnc_controller_series": op.machine.cnc_controller_series,
#                 "calibration_date": op.machine.calibration_date,
#                 "calibration_due_date": op.machine.calibration_due_date,
#                 "last_maintenance_date": op.machine.last_maintenance_date
#             }
#
#         operation_statuses.append({
#             "operation_id": op.id,
#             "operation_number": op.operation_number,
#             "description": op.operation_description,
#             "work_center": {
#                 "code": op.work_center.code,
#                 "name": op.work_center.work_center_name,
#                 "description": op.work_center.description,
#                 "is_schedulable": op.work_center.is_schedulable,
#                 "plant_id": op.work_center.plant_id
#             },
#             "machine": machine_info,
#             "can_log": can_log,
#             "validation_reason": validation_reason,
#             "completed_quantity": total_completed,
#             "rejected_quantity": total_rejected,
#             "required_quantity": order.required_quantity,
#             "remaining_quantity": max(0, order.required_quantity - total_completed),
#             "is_complete": total_completed >= order.required_quantity,
#             "setup_time": float(op.setup_time),
#             "ideal_cycle_time": float(op.ideal_cycle_time),
#             "total_operation_time": float(op.setup_time + (op.ideal_cycle_time * order.required_quantity))
#         })
#
#     # Get raw material information
#     raw_material_info = None
#     if order.raw_material:
#         raw_material_info = {
#             "id": order.raw_material.id,
#             "material_code": getattr(order.raw_material, 'material_code', None),
#             "material_name": getattr(order.raw_material, 'material_name', None),
#             "description": getattr(order.raw_material, 'description', None),
#             "material_type": getattr(order.raw_material, 'material_type', None),
#             "unit_of_measure": getattr(order.raw_material, 'unit_of_measure', None),
#             "standard_cost": getattr(order.raw_material, 'standard_cost', None),
#             "supplier": getattr(order.raw_material, 'supplier', None),
#             "specifications": getattr(order.raw_material, 'specifications', None)
#         }
#
#     # Get project information
#     project_info = None
#     if order.project:
#         project_info = {
#             "id": order.project.id,
#             "name": order.project.name,
#             "priority": order.project.priority,
#             "start_date": order.project.start_date,
#             "end_date": order.project.end_date,
#             "delivery_date": order.project.delivery_date
#         }
#
#     # Calculate overall order completion
#     total_operations = len(operation_statuses)
#     completed_operations = sum(1 for op in operation_statuses if op["is_complete"])
#     overall_completion_percentage = (completed_operations / total_operations * 100) if total_operations > 0 else 0
#
#     # Calculate total production time
#     total_setup_time = sum(op["setup_time"] for op in operation_statuses)
#     total_cycle_time = sum(float(op["ideal_cycle_time"]) * order.required_quantity for op in operation_statuses)
#     total_production_time = total_setup_time + total_cycle_time
#
#     return {
#         "production_order": order.production_order,
#         "sale_order": order.sale_order,
#         "wbs_element": order.wbs_element,
#         "part_number": order.part_number,
#         "part_description": order.part_description,
#         "plant_id": order.plant_id,
#         "total_operations": order.total_operations,
#         "required_quantity": order.required_quantity,
#         "launched_quantity": order.launched_quantity,
#         "raw_material": raw_material_info,
#         "project": project_info,
#         "operations": operation_statuses,
#         "summary": {
#             "total_operations": total_operations,
#             "completed_operations": completed_operations,
#             "remaining_operations": total_operations - completed_operations,
#             "overall_completion_percentage": round(overall_completion_percentage, 2),
#             "total_setup_time": total_setup_time,
#             "total_cycle_time": total_cycle_time,
#             "total_production_time": total_production_time,
#             "estimated_completion_hours": total_production_time / 60 if total_production_time > 0 else 0
#         }
#     }


@router.get("/production-order-operations-status/{production_order}")
@db_session
def get_production_order_operations_status(production_order: str):
    """
    Get the status of all operations for a specific production order.
    Shows which operations are ready for logging.
    """
    order = Order.get(production_order=production_order)
    if not order:
        raise HTTPException(status_code=404, detail="Production order not found")

    operations = select(op for op in Operation if op.order == order).order_by(Operation.operation_number)
    operation_statuses = []

    for op in operations:
        logs = select(log for log in ProductionLog if log.operation == op)
        total_completed = sum(log.quantity_completed or 0 for log in logs)
        total_rejected = sum(log.quantity_rejected or 0 for log in logs)
        can_log, validation_reason = validate_operation_sequence(op.id)

        operation_statuses.append({
            "operation_id": op.id,
            "operation_number": op.operation_number,
            "description": op.operation_description,
            "work_center": op.work_center.work_center_name or op.work_center.code,
            "work_center_schedulable": op.work_center.is_schedulable,
            "can_log": can_log,
            "validation_reason": validation_reason,
            "completed_quantity": total_completed,
            "rejected_quantity": total_rejected,
            "required_quantity": order.launched_quantity,
            "remaining_quantity": max(0, order.launched_quantity - total_completed),
            "is_complete": total_completed >= order.launched_quantity,
            "setup_time": float(op.setup_time),  # Convert Decimal to float
            "ideal_cycle_time": float(op.ideal_cycle_time),  # Convert Decimal to float
            "operation_time": float(op.ideal_cycle_time) * order.launched_quantity  # Calculate total operation time
        })


    return {
        "production_order": order.production_order,
        "part_number": order.part_number,
        "priority": order.project.priority,
        "project": order.project.name,
        "required_quantity": order.launched_quantity,
        "sale_order": order.sale_order,
        "operations": operation_statuses
    }


@router.get("/production-order-operations-status/{work_center_id}/{production_order}")
@db_session
def get_production_order_operations_status(work_center_id: int, production_order: str):
    """
    Get the status of all operations for a specific production order filtered by work center.
    Shows only operations that belong to the specified work center and have machines assigned.
    """
    # First, check if the work center exists
    work_center = WorkCenter.get(id=work_center_id)
    if not work_center:
        raise HTTPException(status_code=404, detail="Work center not found")

    # Check if work center has machines
    machines_in_work_center = select(m for m in Machine if m.work_center == work_center)
    if not machines_in_work_center:
        raise HTTPException(status_code=404, detail="No machines found for this work center")

    # Get the order
    order = Order.get(production_order=production_order)
    if not order:
        raise HTTPException(status_code=404, detail="Production order not found")

    # Get operations that belong to the specified work center and have machines assigned
    operations = select(
        op for op in Operation
        if op.order == order
        and op.work_center == work_center
        and op.machine in machines_in_work_center
    ).order_by(Operation.operation_number)

    if not operations:
        raise HTTPException(
            status_code=404,
            detail=f"No operations found for production order {production_order} in work center {work_center_id} with assigned machines"
        )

    operation_statuses = []

    for op in operations:
        logs = select(log for log in ProductionLog if log.operation == op)
        total_completed = sum(log.quantity_completed or 0 for log in logs)
        total_rejected = sum(log.quantity_rejected or 0 for log in logs)
        is_complete = total_completed >= order.required_quantity

        # Get validation from sequence check
        can_log, validation_reason = validate_operation_sequence(op.id)

        # Override can_log to False if operation is already completed
        if is_complete:
            can_log = False
            validation_reason = "Operation is already completed"

        operation_statuses.append({
            "operation_id": op.id,
            "operation_number": op.operation_number,
            "description": op.operation_description,
            "work_center_id": op.work_center.id,
            "work_center_code": op.work_center.code,
            "work_center_name": op.work_center.work_center_name or op.work_center.code,
            "work_center_schedulable": op.work_center.is_schedulable,
            # "machine_id": op.machine.id,
            # "machine_type": op.machine.type,
            # "machine_make": op.machine.make,
            # "machine_model": op.machine.model,
            "can_log": can_log,
            "validation_reason": validation_reason,
            "completed_quantity": total_completed,
            "rejected_quantity": total_rejected,
            "required_quantity": order.required_quantity,
            "remaining_quantity": max(0, order.required_quantity - total_completed),
            "is_complete": is_complete,
            "setup_time": float(op.setup_time),  # Convert Decimal to float
            "ideal_cycle_time": float(op.ideal_cycle_time),  # Convert Decimal to float
            "operation_time": float(op.ideal_cycle_time) * order.required_quantity  # Calculate total operation time
        })

    return {
        "work_center_id": work_center.id,
        "work_center_code": work_center.code,
        "work_center_name": work_center.work_center_name or work_center.code,
        "production_order": order.production_order,
        "part_number": order.part_number,
        "priority": order.project.priority,
        "project": order.project.name,
        "required_quantity": order.required_quantity,
        "sale_order": order.sale_order,
        "total_operations_in_work_center": len(operation_statuses),
        "operations": operation_statuses
    }