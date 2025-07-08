from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pony.orm import db_session, select, desc
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
import time
from functools import lru_cache

from app.api.v1.endpoints.dynamic_rescheduling import get_combined_schedule
from app.api.v1.endpoints.operatorlog2 import validate_operation_sequence
from app.models import ScheduleVersion, Order, Operation, ProductionLog

router = APIRouter(prefix="/api/v1/scheduling", tags=["scheduling"])

# Thread pool for database operations
executor = ThreadPoolExecutor(max_workers=8)

# Simple in-memory cache
_cache = {}
_cache_timeout = {}
CACHE_TTL = 300  # 5 minutes


def get_from_cache(key: str):
    """Get data from cache if not expired"""
    if key in _cache and key in _cache_timeout:
        if time.time() < _cache_timeout[key]:
            return _cache[key]
        else:
            # Clean expired cache
            _cache.pop(key, None)
            _cache_timeout.pop(key, None)
    return None


def set_cache(key: str, value, ttl: int = CACHE_TTL):
    """Set data in cache with TTL"""
    _cache[key] = value
    _cache_timeout[key] = time.time() + ttl


async def get_combined_schedule_cached():
    # """Get combined schedule with caching"""
    # cache_key = "combined_schedule"
    # cached_data = get_from_cache(cache_key)
    #
    # if cached_data is not None:
    #     return cached_data

    # If not in cache, fetch from source
    try:
        combined_data = await get_combined_schedule()
        # set_cache(cache_key, combined_data)
        return combined_data
    except Exception as e:
        # If main call fails, try to return stale cache data if available
        # if cache_key in _cache:
        #     return _cache[cache_key]
        raise e


def process_reschedule_data_sync(reschedule_data):
    """Synchronous processing of reschedule data"""
    part_production_end_times = {}
    data_sources = {}

    if not reschedule_data:
        return part_production_end_times, data_sources

    for update in reschedule_data:
        try:
            part_number = getattr(update, 'part_number', None)
            production_order = getattr(update, 'production_order', None)
            end_time_str = getattr(update, 'end_time', None)

            if not all([part_number, production_order, end_time_str]):
                continue

            if isinstance(end_time_str, str):
                end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
            else:
                end_time = end_time_str

            key = (part_number, production_order)
            if key not in part_production_end_times or end_time > part_production_end_times[key]:
                part_production_end_times[key] = end_time
                data_sources[key] = "reschedule"
        except (ValueError, TypeError, AttributeError) as e:
            print(f"Error processing reschedule update: {str(e)}")
            continue

    return part_production_end_times, data_sources


def process_scheduled_operations_sync(scheduled_operations, existing_end_times, existing_sources):
    """Synchronous processing of scheduled operations"""
    if not scheduled_operations:
        return existing_end_times, existing_sources

    local_end_times = existing_end_times.copy()
    local_sources = existing_sources.copy()

    for op in scheduled_operations:
        try:
            part_number = getattr(op, 'component', None)
            production_order = getattr(op, 'production_order', None)
            end_time = getattr(op, 'end_time', None)

            if not all([part_number, production_order, end_time]):
                continue

            if isinstance(end_time, str):
                end_time = datetime.fromisoformat(end_time.replace('Z', '+00:00'))

            key = (part_number, production_order)
            # Only update if this key doesn't already have reschedule data
            if key not in local_end_times or (
                    local_sources.get(key) != "reschedule" and end_time > local_end_times[key]
            ):
                local_end_times[key] = end_time
                local_sources[key] = "scheduled"
        except (ValueError, TypeError, AttributeError) as e:
            print(f"Error processing scheduled operation: {str(e)}")
            continue

    return local_end_times, local_sources


def determine_completed_parts_sync(combined_data):
    """Synchronous determination of completed parts"""
    completed_parts = set()

    if not (hasattr(combined_data, 'production_logs') and combined_data.production_logs):
        return completed_parts

    try:
        # Group logs by part efficiently
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

        # Get scheduled operations efficiently
        scheduled_ops_by_part = {}
        if hasattr(combined_data, 'scheduled_operations') and combined_data.scheduled_operations:
            for op in combined_data.scheduled_operations:
                if not (hasattr(op, 'component') and hasattr(op, 'production_order')):
                    continue

                part_key = (op.component, op.production_order)
                if part_key not in scheduled_ops_by_part:
                    scheduled_ops_by_part[part_key] = []
                scheduled_ops_by_part[part_key].append(op)

        # Check completion status
        for part_key, logs in logs_by_part.items():
            if part_key not in scheduled_ops_by_part:
                continue

            try:
                total_operations = len(scheduled_ops_by_part[part_key])
                completed_operations = len(set(
                    getattr(log, 'operation_description', '') for log in logs
                    if hasattr(log, 'operation_description') and log.operation_description
                ))

                if completed_operations < total_operations:
                    continue

                # Quick quantity check
                all_quantities_completed = True
                for op in scheduled_ops_by_part[part_key]:
                    planned_qty = 0
                    quantity_str = getattr(op, 'quantity', '')

                    if isinstance(quantity_str, str) and "Process" in quantity_str:
                        match = re.search(r'Process\(\d+/(\d+)pcs', quantity_str)
                        if match:
                            planned_qty = int(match.group(1))

                    op_description = getattr(op, 'description', None)
                    if not op_description or planned_qty == 0:
                        continue

                    completed_qty = sum(
                        getattr(log, 'quantity_completed', 0) for log in logs
                        if (hasattr(log, 'operation_description') and
                            hasattr(log, 'quantity_completed') and
                            log.operation_description == op_description)
                    )

                    if completed_qty < planned_qty:
                        all_quantities_completed = False
                        break

                if all_quantities_completed:
                    completed_parts.add(part_key)

            except Exception as e:
                print(f"Error checking completion for {part_key}: {str(e)}")
                continue

    except Exception as e:
        print(f"Error in determine_completed_parts_sync: {str(e)}")

    return completed_parts


def get_active_parts_sync(combined_data):
    """Synchronous extraction of active parts"""
    active_parts = set()

    if not (hasattr(combined_data, 'active_parts') and combined_data.active_parts):
        return active_parts

    try:
        for part in combined_data.active_parts:
            if (hasattr(part, 'part_number') and
                    hasattr(part, 'production_order') and
                    getattr(part, 'status', None) == 'active'):
                active_parts.add((part.part_number, part.production_order))
    except Exception as e:
        print(f"Error extracting active parts: {str(e)}")

    return active_parts


async def process_all_data(combined_data):
    """Process all data using thread pool"""
    loop = asyncio.get_event_loop()

    # Create all tasks for parallel execution
    tasks = [
        loop.run_in_executor(executor, get_active_parts_sync, combined_data),
        loop.run_in_executor(executor, determine_completed_parts_sync, combined_data)
    ]

    # Add reschedule processing if data exists
    reschedule_data = getattr(combined_data, 'reschedule', None)
    if reschedule_data:
        tasks.append(
            loop.run_in_executor(executor, process_reschedule_data_sync, reschedule_data)
        )

    # Execute all tasks concurrently
    if reschedule_data:
        active_parts, completed_parts, (part_production_end_times, data_sources) = await asyncio.gather(*tasks)
    else:
        active_parts, completed_parts = await asyncio.gather(*tasks)
        part_production_end_times, data_sources = {}, {}

    # Process scheduled operations
    scheduled_operations = getattr(combined_data, 'scheduled_operations', None)
    if scheduled_operations:
        part_production_end_times, data_sources = await loop.run_in_executor(
            executor,
            process_scheduled_operations_sync,
            scheduled_operations,
            part_production_end_times,
            data_sources
        )

    return active_parts, completed_parts, part_production_end_times, data_sources


@router.get("/part-production-pdc", response_model=List[Dict[str, Any]])
async def get_part_production_pdc():
    """
    Get the Probable Date of Completion (PDC) for each part number and production order.

    Optimized for performance with caching, parallel processing, and proper async handling.
    """
    start_time = time.time()

    try:
        # Step 1: Get combined data (with caching)
        combined_data = await get_combined_schedule_cached()

        if not combined_data:
            return []

        # Step 2: Process all data in parallel
        active_parts, completed_parts, part_production_end_times, data_sources = await process_all_data(combined_data)

        # Step 3: Build result efficiently
        result = []
        processed_parts = set()

        # Add parts with PDC data
        for (part_number, production_order), pdc in part_production_end_times.items():
            result.append({
                "part_number": part_number,
                "production_order": production_order,
                "pdc": pdc.isoformat() if isinstance(pdc, datetime) else str(pdc),
                "status": "completed" if (part_number, production_order) in completed_parts else "in_progress",
                "data_source": data_sources.get((part_number, production_order), "unknown")
            })
            processed_parts.add((part_number, production_order))

        # Add active parts without PDC data
        missing_active_parts = active_parts - processed_parts
        for part_number, production_order in missing_active_parts:
            result.append({
                "part_number": part_number,
                "production_order": production_order,
                "pdc": None,
                "status": "pending",
                "data_source": "none"
            })

        # Sort result
        result.sort(key=lambda x: (x["part_number"], x["production_order"]))

        end_time = time.time()
        print(f"PDC endpoint completed in {end_time - start_time:.2f} seconds")

        return result

    except Exception as e:
        print(f"Error retrieving PDC data: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Error calculating PDC: {str(e)}"
        )




from fastapi import HTTPException
from typing import List, Dict, Any
from datetime import datetime
import time
import traceback

# @router.get("/part-production-pdc12", response_model=List[Dict[str, Any]])
# async def get_part_production_pdc(part_number: str, production_order: str):
#     """
#     Get the Probable Date of Completion (PDC) for a specific part number and production order.
#     """
#     start_time = time.time()
#     print(f"Received request with part_number={part_number}, production_order={production_order}")
#
#     try:
#         # Step 1: Get combined data (with caching)
#         combined_data = await get_combined_schedule_cached()
#
#         # DEBUG: log type and attributes of the response
#         print(f"Combined data type: {type(combined_data)}")
#
#         if hasattr(combined_data, '__dict__'):
#             print("Combined data fields:")
#             for key, value in combined_data.__dict__.items():
#                 if isinstance(value, list):
#                     print(f"  - {key}: list of {len(value)} items")
#                 else:
#                     print(f"  - {key}: {type(value).__name__}")
#         else:
#             print(f"Combined data raw: {combined_data}")
#
#         # Optional: check if a known attribute exists and print length
#         if hasattr(combined_data, 'schedules'):
#             print(f"Schedules count: {len(combined_data.schedules)}")
#         elif hasattr(combined_data, 'items'):
#             print(f"Items count: {len(combined_data.items)}")
#
#         # Step 2: Process all data in parallel
#         active_parts, completed_parts, part_production_end_times, data_sources = await process_all_data(combined_data)
#         print(f"Processed data - active_parts: {len(active_parts)}, completed_parts: {len(completed_parts)}, "
#               f"part_production_end_times: {len(part_production_end_times)}")
#
#         # Step 3: Filter for the specific part and production order
#         result = []
#         target_key = (part_number, production_order)
#         print(f"Looking up target_key: {target_key}")
#
#         if target_key in part_production_end_times:
#             pdc = part_production_end_times[target_key]
#             print(f"Found PDC for {target_key}: {pdc}")
#             result.append({
#                 "part_number": part_number,
#                 "production_order": production_order,
#                 "pdc": pdc.isoformat() if isinstance(pdc, datetime) else str(pdc),
#                 "status": "completed" if target_key in completed_parts else "in_progress",
#                 "data_source": data_sources.get(target_key, "unknown")
#             })
#         elif target_key in active_parts:
#             print(f"{target_key} is active but no PDC data available.")
#             result.append({
#                 "part_number": part_number,
#                 "production_order": production_order,
#                 "pdc": None,
#                 "status": "pending",
#                 "data_source": "none"
#             })
#         else:
#             print(f"DEBUG: {target_key} not found in either part_production_end_times or active_parts — returning empty list")
#
#         end_time = time.time()
#         print(f"PDC endpoint completed in {end_time - start_time:.2f} seconds. Result count: {len(result)}")
#
#         return result
#
#     except Exception as e:
#         print(f"Error retrieving PDC data: {str(e)}")
#         traceback.print_exc()
#         raise HTTPException(
#             status_code=500,
#             detail=f"Error calculating PDC: {str(e)}"
#         )

"""CURRENT PDC """
@router.get("/part-production-pdc12", response_model=List[Dict[str, Any]])
async def get_filtered_part_production(part_number: str, production_order: str):
    """
    Get filtered production data for a specific part number and production order.
    Returns all matching records from the combined schedule data.
    """
    start_time = time.time()
    print(f"Received filtered request with part_number={part_number}, production_order={production_order}")

    try:
        # Step 1: Get combined data (with caching)
        combined_data = await get_combined_schedule_cached()

        print(f"Combined data type: {type(combined_data)}")

        # Step 2: Process all data in parallel
        active_parts, completed_parts, part_production_end_times, data_sources = await process_all_data(combined_data)
        print(f"Processed data - active_parts: {len(active_parts)}, completed_parts: {len(completed_parts)}, "
              f"part_production_end_times: {len(part_production_end_times)}")

        # Step 3: Filter all data for the specific part and production order
        result = []

        # Filter active parts
        matching_active = [
            key for key in active_parts
            if key[0] == part_number and key[1] == production_order
        ]

        # Filter completed parts
        matching_completed = [
            key for key in completed_parts
            if key[0] == part_number and key[1] == production_order
        ]

        # Filter part production end times
        matching_end_times = {
            key: value for key, value in part_production_end_times.items()
            if key[0] == part_number and key[1] == production_order
        }

        print(f"Found {len(matching_active)} active, {len(matching_completed)} completed, "
              f"{len(matching_end_times)} with end times")

        # Step 4: Build result from all matching data
        all_matching_keys = set(matching_active + matching_completed + list(matching_end_times.keys()))

        for key in all_matching_keys:
            part_num, prod_order = key

            # Determine status
            if key in completed_parts:
                status = "completed"
            elif key in active_parts:
                status = "in_progress"
            else:
                status = "pending"

            # Get PDC if available
            pdc = matching_end_times.get(key)
            pdc_value = pdc.isoformat() if isinstance(pdc, datetime) else str(pdc) if pdc else None

            result.append({
                "part_number": part_num,
                "production_order": prod_order,
                "pdc": pdc_value,
                "status": status,
                "data_source": data_sources.get(key, "unknown")
            })

        # Step 5: If no matches found, return empty result with info
        if not result:
            print(f"No matching records found for part_number={part_number}, production_order={production_order}")

        end_time = time.time()
        print(f"Filtered PDC endpoint completed in {end_time - start_time:.2f} seconds. Result count: {len(result)}")

        return result

    except Exception as e:
        print(f"Error retrieving filtered production data: {str(e)}")
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Error filtering production data: {str(e)}"
        )

"""NEW PDC CODE"""
@router.get("/part-production-pdc11", response_model=List[Dict[str, Any]])
async def get_part_production_pdc2(part_number: str, production_order: str):
    """
    Get the Probable Date of Completion (PDC) for each part number and production order.

    Optimized for performance with caching, parallel processing, and proper async handling.
    """
    start_time = time.time()

    try:
        # Step 1: Get combined data (with caching)
        combined_data = await get_combined_schedule_cached()

        if not combined_data:
            return []

        # Step 2: Process all data in parallel
        active_parts, completed_parts, part_production_end_times, data_sources = await process_all_data(combined_data)

        # Step 3: Build result efficiently
        result = []
        processed_parts = set()

        # Add parts with PDC data
        for (part_number, production_order), pdc in part_production_end_times.items():
            result.append({
                "part_number": part_number,
                "production_order": production_order,
                "pdc": pdc.isoformat() if isinstance(pdc, datetime) else str(pdc),
                "status": "completed" if (part_number, production_order) in completed_parts else "in_progress",
                "data_source": data_sources.get((part_number, production_order), "unknown")
            })
            processed_parts.add((part_number, production_order))

        # Add active parts without PDC data
        missing_active_parts = active_parts - processed_parts
        for part_number, production_order in missing_active_parts:
            result.append({
                "part_number": part_number,
                "production_order": production_order,
                "pdc": None,
                "status": "pending",
                "data_source": "none"
            })

        # Sort result
        result.sort(key=lambda x: (x["part_number"], x["production_order"]))

        result1 = []

        for i in result:
            if i["part_number"] == part_number and i["production_order"] == production_order:
                result1.append(i)


        end_time = time.time()
        print(f"PDC endpoint completed in {end_time - start_time:.2f} seconds")

        return result1

    except Exception as e:
        print(f"Error retrieving PDC data: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Error calculating PDC: {str(e)}"
        )


# Optional: Add endpoint to clear cache
@router.post("/clear-cache")
async def clear_pdc_cache():
    """Clear the PDC cache for testing/debugging"""
    global _cache, _cache_timeout
    _cache.clear()
    _cache_timeout.clear()
    return {"message": "Cache cleared successfully"}

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


from fastapi import HTTPException
from pony.orm import db_session, select
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Dict, Any

# Thread pool executor for database operations
executor = ThreadPoolExecutor(max_workers=10)


@router.get("/check-order-completion-simple/{part_number}/{production_order}")
async def check_order_completion_status_simple(part_number: str, production_order: str):
    """
    Simplified version - Check if all operations for a production order are completed.
    Returns basic completion status with overall completion date.
    Optimized with async/await for faster performance.
    """

    def execute_db_operations():
        """Execute all database operations in a single session"""
        with db_session:
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
            operations_list = list(operations)

            if not operations_list:
                raise HTTPException(status_code=404, detail="No operations found for this production order")

            # Process all operations
            all_eligible_operations_completed = True
            completed_count = 0
            eligible_operations = []
            all_completion_end_times = []

            for op in operations_list:
                logs = select(log for log in ProductionLog if log.operation == op)
                logs_list = list(logs)

                operation_completed_qty = sum(log.quantity_completed or 0 for log in logs_list)
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
                        for log in logs_list:
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
                "total_all_operations": len(operations_list),
                "completion_percentage": round((completed_count / total_eligible) * 100,
                                               2) if total_eligible > 0 else 0,
                "overall_completion_date": overall_completion_date,
                "completion_date_status": "Fully Completed" if all_eligible_operations_completed and overall_completion_date else "In Progress"
            }

    # Execute database operations asynchronously
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(executor, execute_db_operations)
        return result
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        # Handle any other exceptions
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


# Alternative version without async if the above still causes issues
@router.get("/check-order-completion-simple-sync/{part_number}/{production_order}")
@db_session
def check_order_completion_status_simple_sync(part_number: str, production_order: str):
    """
    Simplified version - Check if all operations for a production order are completed.
    Returns basic completion status with overall completion date.
    Synchronous version for stability.
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
