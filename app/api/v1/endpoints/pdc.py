from fastapi import APIRouter, Depends, HTTPException
from pony.orm import db_session, select
from typing import List, Dict, Any
from datetime import datetime

from app.api.v1.endpoints.dynamic_rescheduling import get_combined_schedule
from app.models import ScheduleVersion

router = APIRouter(prefix="/api/v1/scheduling", tags=["scheduling"])


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
        result = []
        part_production_end_times = {}  # {(part_number, production_order): end_time}
        data_sources = {}  # Track where the PDC data came from

        try:
            # Get data from the reschedule-actual-planned-combined endpoint
            combined_data = await get_combined_schedule()
        except Exception as e:
            # Handle any exceptions from get_combined_schedule
            print(f"Error calling get_combined_schedule: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Error during combined scheduling: {str(e)}"
            )

        # First, get all active parts that we need to report on
        active_parts = set()
        if hasattr(combined_data, 'active_parts') and combined_data.active_parts:
            for part in combined_data.active_parts:
                if hasattr(part, 'part_number') and hasattr(part, 'production_order') and part.status == 'active':
                    active_parts.add((part.part_number, part.production_order))

        # Process the reschedule updates to find the latest end times
        if hasattr(combined_data, 'reschedule') and combined_data.reschedule:
            for update in combined_data.reschedule:
                # Handle the update as a Pydantic model, not a dictionary
                part_number = update.part_number if hasattr(update, 'part_number') else None
                production_order = update.production_order if hasattr(update, 'production_order') else None
                end_time_str = update.end_time if hasattr(update, 'end_time') else None

                if not part_number or not production_order or not end_time_str:
                    continue

                # Convert string end time to datetime
                try:
                    if isinstance(end_time_str, str):
                        end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
                    else:
                        end_time = end_time_str

                    key = (part_number, production_order)
                    if key not in part_production_end_times or end_time > part_production_end_times[key]:
                        part_production_end_times[key] = end_time
                        data_sources[key] = "reschedule"
                except (ValueError, TypeError) as e:
                    print(f"Error parsing end time {end_time_str}: {str(e)}")
                    continue

        # Use planned schedule data for parts that don't have reschedule data
        if hasattr(combined_data, 'scheduled_operations') and combined_data.scheduled_operations:
            for op in combined_data.scheduled_operations:
                part_number = op.component if hasattr(op, 'component') else None
                production_order = op.production_order if hasattr(op, 'production_order') else None
                end_time = op.end_time if hasattr(op, 'end_time') else None

                if not part_number or not production_order or not end_time:
                    continue

                try:
                    if isinstance(end_time, str):
                        end_time = datetime.fromisoformat(end_time.replace('Z', '+00:00'))

                    key = (part_number, production_order)
                    # Only update if this key doesn't already have reschedule data
                    if key not in part_production_end_times or (
                            data_sources.get(key) != "reschedule" and end_time > part_production_end_times[key]
                    ):
                        part_production_end_times[key] = end_time
                        data_sources[key] = "scheduled"
                except (ValueError, TypeError) as e:
                    print(f"Error parsing end time for scheduled operation: {str(e)}")
                    continue

        # Now determine completion status from the combined data
        completed_parts = set()
        if hasattr(combined_data, 'production_logs') and combined_data.production_logs:
            # Group logs by part number and production order
            logs_by_part = {}
            for log in combined_data.production_logs:
                if not hasattr(log, 'part_number'):
                    continue

                part_number = log.part_number
                # Handle case where production_order attribute might be missing
                production_order = log.production_order if hasattr(log, 'production_order') else ""

                part_key = (part_number, production_order)
                if part_key not in logs_by_part:
                    logs_by_part[part_key] = []
                logs_by_part[part_key].append(log)

            # Get all scheduled operations to determine total quantities
            scheduled_ops_by_part = {}
            if hasattr(combined_data, 'scheduled_operations'):
                for op in combined_data.scheduled_operations:
                    if not hasattr(op, 'component') or not hasattr(op, 'production_order'):
                        continue

                    part_key = (op.component, op.production_order)
                    if part_key not in scheduled_ops_by_part:
                        scheduled_ops_by_part[part_key] = []
                    scheduled_ops_by_part[part_key].append(op)

            # Determine which parts are completed
            for part_key, logs in logs_by_part.items():
                part_number, production_order = part_key

                # Check if all operations have been completed
                if part_key in scheduled_ops_by_part:
                    total_operations = len(scheduled_ops_by_part[part_key])
                    # Make sure operation_description exists before collecting
                    completed_operations = len(set(
                        log.operation_description for log in logs
                        if hasattr(log, 'operation_description')
                    ))

                    # Check quantities from logs against planned quantities
                    all_quantities_completed = True
                    for op in scheduled_ops_by_part[part_key]:
                        # Extract planned quantity from the quantity string
                        import re
                        planned_qty = 0
                        if hasattr(op, 'quantity') and isinstance(op.quantity, str) and "Process" in op.quantity:
                            match = re.search(r'Process\(\d+/(\d+)pcs', op.quantity)
                            if match:
                                planned_qty = int(match.group(1))

                        op_description = op.description if hasattr(op, 'description') else None
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

        # Format the final result
        processed_parts = set()

        # First add all parts with end times from either reschedule or scheduled data
        for (part_number, production_order), pdc in part_production_end_times.items():
            result.append({
                "part_number": part_number,
                "production_order": production_order,
                "pdc": pdc.isoformat() if isinstance(pdc, datetime) else str(pdc),
                "status": "completed" if (part_number, production_order) in completed_parts else "in_progress",
                "data_source": data_sources.get((part_number, production_order), "unknown")
            })
            processed_parts.add((part_number, production_order))

        # Then add any active parts that don't have PDC data
        missing_active_parts = active_parts - processed_parts
        for part_number, production_order in missing_active_parts:
            # Try to get missing PDC data from the scheduling algorithm if available
            pdc_date = None

            # For now, we'll just add the part with a null PDC
            result.append({
                "part_number": part_number,
                "production_order": production_order,
                "pdc": pdc_date,
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