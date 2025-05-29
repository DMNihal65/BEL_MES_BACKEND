from fastapi import APIRouter, Depends, HTTPException
from pony.orm import db_session, select, desc
from typing import List, Dict, Any
from datetime import datetime
import asyncio
import re
from concurrent.futures import ThreadPoolExecutor

from app.api.v1.endpoints.dynamic_rescheduling import get_combined_schedule
from app.api.v1.endpoints.operatorlog2 import validate_operation_sequence
from app.models import ScheduleVersion, Order, Operation, ProductionLog

router = APIRouter(prefix="/api/v1/scheduling", tags=["scheduling"])

# Thread pool for CPU-intensive operations
executor = ThreadPoolExecutor(max_workers=4)


async def process_reschedule_data(reschedule_data):
    """Process reschedule data asynchronously"""
    part_production_end_times = {}
    data_sources = {}

    def process_reschedule_updates():
        local_end_times = {}
        local_sources = {}

        for update in reschedule_data:
            part_number = getattr(update, 'part_number', None)
            production_order = getattr(update, 'production_order', None)
            end_time_str = getattr(update, 'end_time', None)

            if not all([part_number, production_order, end_time_str]):
                continue

            try:
                if isinstance(end_time_str, str):
                    end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
                else:
                    end_time = end_time_str

                key = (part_number, production_order)
                if key not in local_end_times or end_time > local_end_times[key]:
                    local_end_times[key] = end_time
                    local_sources[key] = "reschedule"
            except (ValueError, TypeError) as e:
                print(f"Error parsing end time {end_time_str}: {str(e)}")
                continue

        return local_end_times, local_sources

    # Run CPU-intensive processing in thread pool
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, process_reschedule_updates)


async def process_scheduled_operations(scheduled_operations, existing_end_times, existing_sources):
    """Process scheduled operations data asynchronously"""

    def process_scheduled_ops():
        local_end_times = existing_end_times.copy()
        local_sources = existing_sources.copy()

        for op in scheduled_operations:
            part_number = getattr(op, 'component', None)
            production_order = getattr(op, 'production_order', None)
            end_time = getattr(op, 'end_time', None)

            if not all([part_number, production_order, end_time]):
                continue

            try:
                if isinstance(end_time, str):
                    end_time = datetime.fromisoformat(end_time.replace('Z', '+00:00'))

                key = (part_number, production_order)
                # Only update if this key doesn't already have reschedule data
                if key not in local_end_times or (
                        local_sources.get(key) != "reschedule" and end_time > local_end_times[key]
                ):
                    local_end_times[key] = end_time
                    local_sources[key] = "scheduled"
            except (ValueError, TypeError) as e:
                print(f"Error parsing end time for scheduled operation: {str(e)}")
                continue

        return local_end_times, local_sources

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, process_scheduled_ops)


async def determine_completed_parts(combined_data):
    """Determine completed parts asynchronously"""

    def process_completion_status():
        completed_parts = set()

        if not (hasattr(combined_data, 'production_logs') and combined_data.production_logs):
            return completed_parts

        # Group logs by part number and production order
        logs_by_part = {}
        for log in combined_data.production_logs:
            if not hasattr(log, 'part_number'):
                continue

            part_number = log.part_number
            production_order = getattr(log, 'production_order', "")

            part_key = (part_number, production_order)
            if part_key not in logs_by_part:
                logs_by_part[part_key] = []
            logs_by_part[part_key].append(log)

        # Get all scheduled operations to determine total quantities
        scheduled_ops_by_part = {}
        if hasattr(combined_data, 'scheduled_operations'):
            for op in combined_data.scheduled_operations:
                if not (hasattr(op, 'component') and hasattr(op, 'production_order')):
                    continue

                part_key = (op.component, op.production_order)
                if part_key not in scheduled_ops_by_part:
                    scheduled_ops_by_part[part_key] = []
                scheduled_ops_by_part[part_key].append(op)

        # Determine which parts are completed
        for part_key, logs in logs_by_part.items():
            if part_key not in scheduled_ops_by_part:
                continue

            total_operations = len(scheduled_ops_by_part[part_key])
            completed_operations = len(set(
                log.operation_description for log in logs
                if hasattr(log, 'operation_description')
            ))

            # Check quantities from logs against planned quantities
            all_quantities_completed = True
            for op in scheduled_ops_by_part[part_key]:
                planned_qty = 0
                if hasattr(op, 'quantity') and isinstance(op.quantity, str) and "Process" in op.quantity:
                    match = re.search(r'Process\(\d+/(\d+)pcs', op.quantity)
                    if match:
                        planned_qty = int(match.group(1))

                op_description = getattr(op, 'description', None)
                if not op_description:
                    continue

                # Sum completed quantities for this operation
                completed_qty = sum(
                    log.quantity_completed for log in logs
                    if hasattr(log, 'operation_description') and
                    hasattr(log, 'quantity_completed') and
                    log.operation_description == op_description
                )

                if completed_qty < planned_qty:
                    all_quantities_completed = False
                    break

            if completed_operations >= total_operations and all_quantities_completed:
                completed_parts.add(part_key)

        return completed_parts

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, process_completion_status)


async def get_active_parts(combined_data):
    """Extract active parts asynchronously"""

    def extract_active_parts():
        active_parts = set()
        if hasattr(combined_data, 'active_parts') and combined_data.active_parts:
            for part in combined_data.active_parts:
                if (hasattr(part, 'part_number') and
                        hasattr(part, 'production_order') and
                        getattr(part, 'status', None) == 'active'):
                    active_parts.add((part.part_number, part.production_order))
        return active_parts

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, extract_active_parts)


@router.get("/part-production-pdc", response_model=List[Dict[str, Any]])
async def get_part_production_pdc():
    """
    Get the Probable Date of Completion (PDC) for each part number and production order.

    The PDC is determined primarily from the rescheduled operations in the reschedule-actual-planned-combined endpoint.
    Only falls back to planned schedule data if no reschedule data is available.

    Returns:
    - A list of dictionaries containing part numbers, production orders, and their PDCs
    """
    try:
        # Get combined data first
        try:
            combined_data = await get_combined_schedule()
        except Exception as e:
            print(f"Error calling get_combined_schedule: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Error during combined scheduling: {str(e)}"
            )

        # Process all data concurrently
        tasks = []

        # Task 1: Get active parts
        tasks.append(get_active_parts(combined_data))

        # Task 2: Process reschedule data
        reschedule_task = None
        if hasattr(combined_data, 'reschedule') and combined_data.reschedule:
            reschedule_task = process_reschedule_data(combined_data.reschedule)
            tasks.append(reschedule_task)

        # Task 3: Determine completed parts
        tasks.append(determine_completed_parts(combined_data))

        # Execute all tasks concurrently
        if reschedule_task:
            active_parts, (part_production_end_times, data_sources), completed_parts = await asyncio.gather(*tasks)
        else:
            active_parts, completed_parts = await asyncio.gather(*tasks)
            part_production_end_times, data_sources = {}, {}

        # Process scheduled operations if available
        if hasattr(combined_data, 'scheduled_operations') and combined_data.scheduled_operations:
            part_production_end_times, data_sources = await process_scheduled_operations(
                combined_data.scheduled_operations, part_production_end_times, data_sources
            )

        # Format the final result
        result = []
        processed_parts = set()

        # Add all parts with end times from either reschedule or scheduled data
        for (part_number, production_order), pdc in part_production_end_times.items():
            result.append({
                "part_number": part_number,
                "production_order": production_order,
                "pdc": pdc.isoformat() if isinstance(pdc, datetime) else str(pdc),
                "status": "completed" if (part_number, production_order) in completed_parts else "in_progress",
                "data_source": data_sources.get((part_number, production_order), "unknown")
            })
            processed_parts.add((part_number, production_order))

        # Add any active parts that don't have PDC data
        missing_active_parts = active_parts - processed_parts
        for part_number, production_order in missing_active_parts:
            result.append({
                "part_number": part_number,
                "production_order": production_order,
                "pdc": None,
                "status": "pending",
                "data_source": "none"
            })

        # Sort by part number and production order
        result.sort(key=lambda x: (x["part_number"], x["production_order"]))

        return result

    except Exception as e:
        print(f"Error retrieving PDC data: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Error calculating PDC: {str(e)}"
        )

from pydantic import BaseModel
from typing import Optional


class OrderCompletionRequest(BaseModel):
    part_number: str
    production_order: str


class OrderCompletionResponse(BaseModel):
    is_order_completed: bool
    completion_status: str
    part_number: str
    production_order: str
    total_operations: int
    completed_operations: int
    pending_operations: int
    completion_percentage: float
    project_name: str
    priority: int
    required_quantity: int
    total_completed_quantity: int
    total_rejected_quantity: int
    operations_summary: list
    order_completion_date: Optional[str] = None



@router.post("/check-order-completion-simple/{part_number}/{production_order}")
@db_session
def check_order_completion_status_simple(part_number: str, production_order: str):
    """
    Simplified version - Check if all operations for a production order are completed.
    Returns basic completion status with overall completion date.
    """
    # Get the order
    order = Order.get(production_order=production_order)
    if not order:
        raise HTTPException(status_code=404, detail="Production order not found")

    # Validate part number matches
    if order.part_number != part_number:
        raise HTTPException(
            status_code=400,
            detail=f"Part number mismatch. Expected: {order.part_number}, Provided: {part_number}"
        )

    # Get all operations for this order
    operations = select(op for op in Operation if op.order == order)

    if not operations:
        raise HTTPException(status_code=404, detail="No operations found for this production order")

    # Check if all eligible operations are completed
    all_eligible_operations_completed = True
    completed_count = 0
    eligible_operations = []
    all_completion_end_times = []

    for op in operations:
        logs = select(log for log in ProductionLog if log.operation == op)
        operation_completed_qty = sum(log.quantity_completed or 0 for log in logs)
        is_operation_complete = operation_completed_qty >= order.required_quantity

        # Check if this operation can be logged (sequence validation)
        can_log, validation_reason = validate_operation_sequence(op.id)

        # Override can_log if operation is already completed
        if is_operation_complete:
            can_log = False
            validation_reason = "Operation is already completed"

        # Only consider operations that are either:
        # 1. Currently eligible for logging (can_log = True), OR
        # 2. Already completed (meaning they were previously eligible and now finished)
        is_eligible_operation = can_log or is_operation_complete

        if is_eligible_operation:
            eligible_operations.append(op)
            if is_operation_complete:
                completed_count += 1

                # Collect all end_times from logs for this completed operation
                for log in logs:
                    if log.end_time:
                        all_completion_end_times.append(log.end_time)
            else:
                all_eligible_operations_completed = False

    if not eligible_operations:
        raise HTTPException(
            status_code=400,
            detail="No operations are currently eligible for logging based on sequence validation"
        )

    total_eligible = len(eligible_operations)

    # Calculate overall completion date
    overall_completion_date = None
    if all_eligible_operations_completed and all_completion_end_times:
        # Only set completion date if ALL eligible operations are completed
        overall_completion_date = max(all_completion_end_times)

    return {
        "is_order_completed": all_eligible_operations_completed,
        "message": "ORDER COMPLETED - All eligible operations finished" if all_eligible_operations_completed else f"ORDER IN PROGRESS - {completed_count}/{total_eligible} eligible operations completed",
        "part_number": order.part_number,
        "production_order": order.production_order,
        "project_name": order.project.name,
        "completed_operations": completed_count,
        "total_eligible_operations": total_eligible,
        "total_all_operations": len(operations),
        "completion_percentage": round((completed_count / total_eligible) * 100, 2) if total_eligible > 0 else 0,
        "overall_completion_date": overall_completion_date,
        "completion_date_status": "Fully Completed" if all_eligible_operations_completed and overall_completion_date else "In Progress"
    }


@router.get("/check-order-completion-simple")
@db_session
def get_all_orders_completion_status():
    """
    Get completion status for all production orders.
    Returns list of all orders with their completion status.
    """
    # Get all orders
    all_orders = select(order for order in Order)

    if not all_orders:
        return {
            "message": "No production orders found",
            "orders": []
        }

    completed_orders_status = []

    for order in all_orders:
        # Get all operations for this order
        operations = select(op for op in Operation if op.order == order)

        if not operations:
            # Skip orders with no operations since they can't be completed
            continue

        # Check if all eligible operations are completed
        all_eligible_operations_completed = True
        completed_count = 0
        eligible_operations = []
        all_completion_end_times = []

        for op in operations:
            logs = select(log for log in ProductionLog if log.operation == op)
            operation_completed_qty = sum(log.quantity_completed or 0 for log in logs)
            is_operation_complete = operation_completed_qty >= order.required_quantity

            # Check if this operation can be logged (sequence validation)
            can_log, validation_reason = validate_operation_sequence(op.id)

            # Override can_log if operation is already completed
            if is_operation_complete:
                can_log = False
                validation_reason = "Operation is already completed"

            # Only consider operations that are either:
            # 1. Currently eligible for logging (can_log = True), OR
            # 2. Already completed (meaning they were previously eligible and now finished)
            is_eligible_operation = can_log or is_operation_complete

            if is_eligible_operation:
                eligible_operations.append(op)
                if is_operation_complete:
                    completed_count += 1

                    # Collect all end_times from logs for this completed operation
                    for log in logs:
                        if log.end_time:
                            all_completion_end_times.append(log.end_time)
                else:
                    all_eligible_operations_completed = False

        if not eligible_operations:
            # Skip orders with no eligible operations since they can't be completed
            continue

        total_eligible = len(eligible_operations)

        # Calculate overall completion date
        overall_completion_date = None
        if all_eligible_operations_completed and all_completion_end_times:
            # Only set completion date if ALL eligible operations are completed
            overall_completion_date = max(all_completion_end_times)

        # Only add to results if order is completed
        if all_eligible_operations_completed:
            completed_orders_status.append({
                "part_number": order.part_number,
                "production_order": order.production_order,
                "project_name": order.project.name if order.project else "Unknown",
                "is_order_completed": True,
                "message": "ORDER COMPLETED - All eligible operations finished",
                "completed_operations": completed_count,
                "total_eligible_operations": total_eligible,
                "total_all_operations": len(operations),
                "completion_percentage": 100.0,
                "overall_completion_date": overall_completion_date,
                "completion_date_status": "Fully Completed"
            })

    # Check if any completed orders found
    if not completed_orders_status:
        return {
            "message": "No completed production orders found",
            "completed_orders": []
        }

    # Summary statistics
    total_completed_orders = len(completed_orders_status)

    return {
        "message": f"Retrieved {total_completed_orders} completed production orders",
        "summary": {
            "total_completed_orders": total_completed_orders
        },
        "completed_orders": completed_orders_status
    }
