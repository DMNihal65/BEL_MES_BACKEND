import traceback
import calendar
from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

import pandas as pd
from dateutil import parser
from fastapi import APIRouter, HTTPException, Query
from pony.orm import db_session, select, ObjectNotFound, desc
from pydantic import BaseModel

from app.models import Order, Operation, Machine, PartScheduleStatus, PlannedScheduleItem, ScheduleVersion, \
    ProductionLog, WorkCenter, MachineStatus, ScheduleHistory
from app.crud.pdc import create_pdc_record, get_pdc_by_part_number_and_po, update_pdc_record, \
    delete_pdc_by_production_order
from app.crud.operation import fetch_operations
from app.crud.component_quantities import fetch_component_quantities
from app.crud.leadtime import fetch_lead_times
from app.algorithm.scheduling import schedule_operations
import re

from app.schemas.operations import WorkCenterMachine
from app.schemas.scheduled1 import ScheduleResponse, ProductionLogsResponse, ProductionLogResponse, ScheduledOperation, \
    CombinedScheduleProductionResponse, PartProductionResponse, PartProductionTimeline, PartStatusUpdate, \
    MachineUtilization, OrderCompletionRequest, OrderCompletionResponse, OrderCompletionStatus, \
    AllCompletionStatusResponse, OrderCompletionRecord, PartScheduleStartDateResponse
from app.models.master_order import OrderCompleted

from datetime import time as dt_time

router = APIRouter(prefix="/api/v1/scheduling", tags=["scheduling"])


def overlap_with_shift(downtime_start: datetime, downtime_end: datetime, shift_start_hour: int = 6,
                       shift_end_hour: int = 22) -> float:
    """
    Calculate the overlap in hours between a downtime interval and working shift hours for each day.
    shift_start_hour: hour when shift starts (inclusive)
    shift_end_hour: hour when shift ends (exclusive)
    Returns total overlap in hours (float)
    """
    # Optimize: If downtime is completely outside shift hours, return 0
    if downtime_end <= downtime_start:
        return 0.0

    # Optimize: If downtime spans multiple days, calculate more efficiently
    total_overlap = 0.0
    current = downtime_start

    # Calculate shift duration once
    shift_duration = shift_end_hour - shift_start_hour

    while current < downtime_end:
        # Define shift for this day
        shift_start = current.replace(hour=shift_start_hour, minute=0, second=0, microsecond=0)
        shift_end = current.replace(hour=shift_end_hour, minute=0, second=0, microsecond=0)

        # If shift_end is before shift_start (overnight shift), add a day
        if shift_end <= shift_start:
            shift_end += timedelta(days=1)

        # Calculate overlap for this day
        interval_start = max(current, shift_start)
        interval_end = min(downtime_end, shift_end)

        if interval_start < interval_end:
            total_overlap += (interval_end - interval_start).total_seconds() / 3600

        # Move to next day
        next_day = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        current = next_day

    return total_overlap



@router.post("/set-part-status/{production_order}")
async def set_part_status(production_order: str, status_update: PartStatusUpdate = None, status: str = None):
    """
    Set whether a production order should be included in scheduling
    When setting to 'active', captures the current timestamp for scheduling

    Can accept status either as a query parameter or in the request body
    """
    # Decide which status to use (prefer body over query param)
    final_status = None

    if status_update:
        final_status = status_update.status
    elif status:
        final_status = status
    else:
        raise HTTPException(
            status_code=400,
            detail="Status must be provided either in body or as query parameter"
        )

    if final_status not in ['active', 'inactive']:
        raise HTTPException(
            status_code=400,
            detail="Status must be 'active' or 'inactive'"
        )

    try:
        with db_session:
            # First verify production order exists in master_order
            order = Order.get(production_order=production_order)
            if not order:
                raise HTTPException(
                    status_code=404,
                    detail=f"Production order {production_order} not found in master_order"
                )

            # Find or create status record
            status_record = PartScheduleStatus.get(production_order=production_order)
            # Create full timestamp with both date and time in UTC
            current_time_utc = datetime.utcnow()

            # Convert UTC to IST (UTC+5:30)
            ist_offset = timedelta(hours=5, minutes=30)
            current_time_ist = current_time_utc + ist_offset

            if not status_record:
                # Create new status record (still store UTC in database)
                status_record = PartScheduleStatus(
                    production_order=production_order,
                    part_number=order.part_number,
                    status=final_status,
                    created_at=current_time_utc,
                    updated_at=current_time_utc
                )

                # If creating with inactive status, delete corresponding PDC records
                if final_status == 'inactive':
                    # Ensure start_date is cleared when made inactive
                    status_record.start_date = None
                    try:
                        delete_pdc_by_production_order(production_order)
                    except Exception as pdc_error:
                        # Log the error but don't fail the main operation
                        print(f"Warning: Failed to delete PDC records for {production_order}: {str(pdc_error)}")
            else:
                # Only update the timestamp if changing from inactive to active
                if status_record.status == 'inactive' and final_status == 'active':
                    status_record.updated_at = current_time_utc

                # Always update the status
                status_record.status = final_status

                # If status is being set to inactive, delete corresponding PDC records
                if final_status == 'inactive':
                    # Ensure start_date is cleared when made inactive
                    status_record.start_date = None
                    try:
                        delete_pdc_by_production_order(production_order)
                    except Exception as pdc_error:
                        # Log the error but don't fail the main operation
                        print(f"Warning: Failed to delete PDC records for {production_order}: {str(pdc_error)}")

                # Format the activation timestamp to include both date and time in IST
                activation_time_str = current_time_ist.strftime(
                    "%Y-%m-%d %H:%M:%S") if final_status == 'active' else None

            # Prepare response message
            response_message = f"Production order {production_order} status set to {final_status}"
            if final_status == 'inactive':
                response_message += " and corresponding PDC records have been deleted"

            return {
                "message": response_message,
                "will_be_scheduled": final_status == 'active',
                "activation_time": activation_time_str,
                "part_number": order.part_number
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/active-parts")
async def get_active_parts():
    """Get all parts that are currently marked as active for scheduling"""
    try:
        with db_session:
            active_items = select((
                                      p.production_order,
                                      p.part_number,
                                      p.status,
                                      p.updated_at
                                  ) for p in PartScheduleStatus)[:]

            # Convert UTC to IST (UTC+5:30)
            ist_offset = timedelta(hours=5, minutes=30)

            # Get the required quantities for these production orders
            po_quantities = {}
            for order in Order.select():
                po_quantities[order.production_order] = order.required_quantity

            return {
                "active_parts": [
                    {
                        "production_order": production_order,
                        "part_number": part_number,
                        "status": status,
                        "required_quantity": po_quantities.get(production_order, 0),
                        "activation_time": (updated_at + ist_offset).strftime(
                            "%Y-%m-%d %H:%M:%S") if status == 'active' and updated_at else None
                    }
                    for production_order, part_number, status, updated_at in active_items
                ]
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def extract_quantity(quantity_str: str) -> tuple[int, int, int]:
    """
    Extract quantities from process strings like:
    "Process(85/291pcs)" or "Process(85/291pcs, Today: 85pcs)"

    Returns:
        tuple: (total_quantity, current_quantity, today_quantity)
    """
    try:
        if "Process" in quantity_str:
            # Try to match the format with "Today" first
            match = re.search(r'Process\((\d+)/(\d+)pcs, Today: (\d+)pcs\)', quantity_str)
            if match:
                current_qty = int(match.group(1))
                total_qty = int(match.group(2))
                today_qty = int(match.group(3))
                return total_qty, current_qty, today_qty

            # Match the simple format "Process(85/291pcs)"
            match = re.search(r'Process\((\d+)/(\d+)pcs\)', quantity_str)
            if match:
                current_qty = int(match.group(1))
                total_qty = int(match.group(2))
                return total_qty, current_qty, current_qty

        elif "Setup" in quantity_str:
            return 1, 1, 1

        numbers = re.findall(r'\d+', quantity_str)
        if numbers:
            first_num = int(numbers[0])
            return first_num, first_num, first_num

        return 1, 1, 1

    except Exception as e:
        print(f"Error parsing quantity string: {quantity_str}, Error: {str(e)}")
        return 1, 1, 1


def store_schedule(
        schedule_df,
        component_status,
        schedule_history: Optional["ScheduleHistory"] = None,
        global_version: Optional[int] = None,
        schedule_history_id: Optional[int] = None,
):
    """Store the generated schedule in the database, avoiding duplicate entries
    If schedule_history is provided, link new items to that history.
    If global_version is provided, set PlannedScheduleItem.current_version and ScheduleVersion.version_number to it.
    """
    try:
        stored_items = []

        # Resolve schedule_history from id inside the current session to avoid cross-transaction objects
        resolved_history = None
        if schedule_history_id is not None:
            try:
                resolved_history = ScheduleHistory.get(id=schedule_history_id)
            except Exception:
                resolved_history = None
        elif schedule_history is not None:
            # Fallback: if an entity instance was passed, try to re-fetch by its id to bind to this session
            try:
                resolved_history = ScheduleHistory.get(id=schedule_history.id)
            except Exception:
                resolved_history = None

        for _, row in schedule_df.iterrows():
            part_no = row['partno']
            operation_desc = row['operation']
            machine_id = row['machine_id']
            production_order = row.get('production_order')

            # Find the specific order using both part number and production order
            order = Order.get(part_number=part_no, production_order=production_order)

            if not order:
                # print(f"No order found for part {part_no} with production order {production_order}")
                continue

            # Find the specific operation for this order and operation description
            operation = Operation.select(
                lambda op: op.order == order and op.operation_description == operation_desc
            ).first()

            if not operation:
                # print(f"No operation found for order {order.id} with description {operation_desc}")
                continue

            try:
                machine = Machine[machine_id]
            except ObjectNotFound:
                print(f"Machine with ID {machine_id} not found")
                continue

            total_qty, current_qty, _ = extract_quantity(row['quantity'])

            # print('&' * 50)
            # print(type(row['start_time']))
            # print('&' * 50)

            start_time = row['start_time'].to_pydatetime()
            end_time = row['end_time'].to_pydatetime()

            # Check if this exact schedule already exists within the SAME schedule history only
            existing_schedule = PlannedScheduleItem.select(
                lambda s: s.order == order and
                          s.operation == operation and
                          s.machine == machine and
                          s.initial_start_time == start_time and
                          s.initial_end_time == end_time and
                          s.total_quantity == total_qty and
                          s.schedule_history == resolved_history
            ).first()

            if existing_schedule:
                active_version = existing_schedule.schedule_versions.select(
                    lambda v: v.is_active == True
                ).first()
                if active_version:
                    stored_items.append({
                        'schedule_item_id': existing_schedule.id,
                        'version_id': active_version.id,
                        'total_quantity': total_qty,
                        'current_quantity': current_qty,
                        'status': 'existing'
                    })
                continue

            # Create new schedule item if it doesn't exist
            schedule_item = PlannedScheduleItem(
                order=order,
                operation=operation,
                machine=machine,
                initial_start_time=start_time,
                initial_end_time=end_time,
                total_quantity=total_qty,
                remaining_quantity=total_qty - current_qty,
                status='scheduled',
                current_version=global_version if global_version is not None else 1,
                schedule_history=resolved_history
            )

            schedule_version = ScheduleVersion(
                schedule_item=schedule_item,
                version_number=global_version if global_version is not None else 1,
                planned_start_time=start_time,
                planned_end_time=end_time,
                planned_quantity=total_qty,
                completed_quantity=current_qty,
                remaining_quantity=total_qty - current_qty,
                is_active=True
            )

            stored_items.append({
                'schedule_item_id': schedule_item.id,
                'version_id': schedule_version.id,
                'total_quantity': total_qty,
                'current_quantity': current_qty,
                'status': 'new'
            })

        return stored_items

    except Exception as e:
        print(f"Error storing schedule: {str(e)}")
        traceback.print_exc()
        raise e


@router.get("/schedule-planned/", response_model=ScheduleResponse)
async def schedule():
    """Generate schedule for active parts and store in database"""
    try:
        # Initialize work_centers_data at the start
        work_centers_data = []

        # Always fetch work centers data regardless of active production orders
        with db_session:
            # Fetch work centers and their machines for the response
            for work_center in WorkCenter.select():
                machines_in_wc = []
                for machine in work_center.machines:
                    machines_in_wc.append({
                        "id": str(machine.id),
                        "name": machine.make,
                        "model": machine.model,
                        "type": machine.type
                    })

                work_centers_data.append(
                    WorkCenterMachine(
                        work_center_code=work_center.code,
                        work_center_name=work_center.work_center_name or "",
                        machines=machines_in_wc,
                        is_schedulable=work_center.is_schedulable  # Include the flag in response
                    )
                )

            # Log schedulable work centers
            schedulable_work_centers = [wc for wc in WorkCenter.select() if wc.is_schedulable]
            # print(f"Schedulable work centers: {[wc.code for wc in schedulable_work_centers]}")

            # Get list of schedulable work center IDs for filtering
            schedulable_work_center_ids = {wc.id for wc in schedulable_work_centers}

            # Fetch all production orders with active status
            active_production_orders = select(p.production_order for p in PartScheduleStatus if p.status == 'active')[:]

            # Convert to a set for faster lookups
            active_production_orders_set = set(active_production_orders)

            # print(f"Active production orders: {active_production_orders_set}")

            if not active_production_orders_set:
                # print("No active production orders found")
                return ScheduleResponse(
                    scheduled_operations=[],
                    overall_end_time=datetime.utcnow(),
                    overall_time="0",
                    daily_production={},
                    component_status={},
                    partially_completed=["No parts are marked as active for scheduling"],
                    work_centers=work_centers_data  # Return work centers even if no active orders
                )

            # Get mapping of production orders to part numbers, descriptions, and required quantities
            po_to_part_mapping = {}
            po_to_part_description_mapping = {}  # New mapping for part descriptions
            part_po_to_quantity = {}

            # Get all active part statuses with their required quantities
            active_part_statuses = select(p for p in PartScheduleStatus if p.status == 'active')[:]

            for part_status in active_part_statuses:
                po = part_status.production_order
                part_number = part_status.part_number
                po_to_part_mapping[po] = part_number

                # Get quantity and part description from Order if possible
                order = Order.get(production_order=po, part_number=part_number)
                quantity = order.launched_quantity if order else 0

                # Store the part description in the mapping
                if order and order.part_description:
                    po_to_part_description_mapping[po] = order.part_description
                else:
                    po_to_part_description_mapping[po] = ""  # Empty string if no description

                # If no quantity found in Order, use a default value
                if quantity <= 0:
                    # Try to find the associated Order and get its launched_quantity
                    order = Order.get(part_number=part_number)
                    quantity = order.launched_quantity if order else 10  # Default to 10 if no quantity found

                    # Try to get description from this order as well
                    if order and order.part_description and po not in po_to_part_description_mapping:
                        po_to_part_description_mapping[po] = order.part_description

                # Store the part-PO specific quantity
                part_po_to_quantity[(part_number, po)] = quantity

        # Fetch operations for all parts
        df = fetch_operations()

        if df.empty:
            # print("No operations found in fetch_operations()")
            return ScheduleResponse(
                scheduled_operations=[],
                overall_end_time=datetime.utcnow(),
                overall_time="0",
                daily_production={},
                component_status={},
                partially_completed=["No operations found in database"],
                work_centers=work_centers_data  # Return work centers even if no operations
            )

        # print(f"Original operations dataframe shape: {df.shape}")
        # print(f"Columns in operations dataframe: {df.columns.tolist()}")

        # Add production_order column to dataframe
        if 'production_order' not in df.columns:
            # Maps part numbers to their active production orders
            part_to_pos = {}
            for part_number in df['partno'].unique():
                part_to_pos[part_number] = []
                for po in active_production_orders_set:
                    if po_to_part_mapping.get(po) == part_number:
                        part_to_pos[part_number].append(po)

            # Expand the dataframe to include all active production orders
            # Filter for operations from schedulable work centers and expand the dataframe
            expanded_rows = []
            for (part_number, po), quantity in part_po_to_quantity.items():
                # Query actual operations for this specific (part_number, production_order)
                # CRITICAL FIX: Only include operations from work centers that are marked as schedulable
                matching_ops = Operation.select(
                    lambda o: o.order.part_number == part_number and
                              o.order.production_order == po and
                              o.work_center.is_schedulable == True  # Explicit check for is_schedulable=True
                )

                for op in matching_ops:
                    expanded_rows.append({
                        'partno': part_number,
                        'operation': op.operation_description,
                        'machine_id': op.machine.id,
                        'sequence': op.operation_number,
                        'time': float(op.ideal_cycle_time),
                        'production_order': po,
                        'work_center_id': op.work_center.id  # Add work center ID for filtering
                    })

            if expanded_rows:
                df = pd.DataFrame(expanded_rows)
            else:
                df = pd.DataFrame()  # Empty dataframe if no active production orders found

        # Double check if we have any operations for active production orders
        if df.empty:
            # print("No operations left after filtering for active production orders and schedulable work centers")
            return ScheduleResponse(
                scheduled_operations=[],
                overall_end_time=datetime.utcnow(),
                overall_time="0",
                daily_production={},
                component_status={},
                partially_completed=["No operations found for active production orders in schedulable work centers"],
                work_centers=work_centers_data  # Return work centers even if no operations for active orders
            )

        # Filter to keep only rows with active production orders
        df = df[df['production_order'].isin(active_production_orders_set)]

        # ADDITIONAL FILTER: Ensure all operations are from schedulable work centers
        # Create a mapping from machine_id to work_center_id
        machine_to_wc = {}
        with db_session:
            for machine in Machine.select():
                machine_to_wc[machine.id] = machine.work_center.id

        # Add a column with work center ID for each operation based on its machine
        df['work_center_id'] = df['machine_id'].map(machine_to_wc)

        # Filter out operations from non-schedulable work centers
        df = df[df['work_center_id'].isin(schedulable_work_center_ids)]

        if df.empty:
            # print("No operations left after filtering for schedulable work centers")
            return ScheduleResponse(
                scheduled_operations=[],
                overall_end_time=datetime.utcnow(),
                overall_time="0",
                daily_production={},
                component_status={},
                partially_completed=["All operations are in non-schedulable work centers"],
                work_centers=work_centers_data
            )

        # Get the active part numbers based on the filtered dataframe
        active_part_numbers_in_df = df['partno'].unique().tolist()
        # print(f"Active part numbers in filtered dataframe: {active_part_numbers_in_df}")

        # Create component_quantities dictionary with the correct format
        component_quantities = {}
        for _, row in df.iterrows():
            part_number = row['partno']
            production_order = row['production_order']
            key = (part_number, production_order)

            # Use the saved quantity for this part-PO combination
            if key not in component_quantities and key in part_po_to_quantity:
                component_quantities[key] = part_po_to_quantity[key]

        # print(f"Component quantities for scheduling: {component_quantities}")

        # Get lead times and run scheduling within an active db session
        with db_session:
            lead_times = fetch_lead_times()
        # Filter lead_times to only include parts in filtered operations
        lead_times = {k: v for k, v in lead_times.items() if k in active_part_numbers_in_df}
        # Call scheduling algorithm with filtered dataframe and properly structured component_quantities
        with db_session:
            schedule_df, overall_end_time, overall_time, daily_production, \
                component_status, partially_completed = schedule_operations(
                df, component_quantities, lead_times
            )

        # Final verification
        if not schedule_df.empty:
            # print(f"Final schedule has {len(schedule_df)} operations")
            # print(f"Production orders in final schedule: {schedule_df['production_order'].unique().tolist()}")

            # Verify all production orders in the schedule are active
            scheduled_pos = set(schedule_df['production_order'].unique())
            invalid_pos = scheduled_pos - active_production_orders_set
            if invalid_pos:
                # print(f"WARNING: Found inactive production orders in schedule: {invalid_pos}")
                # Filter out any operations with inactive production orders
                schedule_df = schedule_df[schedule_df['production_order'].isin(active_production_orders_set)]

        # Filter component_status to only include entries with active production orders
        filtered_component_status = {}
        for key, status in component_status.items():
            production_order = None

            # Check if this is a combined key (partno_production_order)
            if '_' in key:
                partno, production_order = key.split('_', 1)
            else:
                production_order = status.get('production_order')

            # Only include if the production_order is active
            if production_order in active_production_orders_set:
                filtered_component_status[key] = status

        # Replace the original component_status with filtered version
        component_status = filtered_component_status
        # print(f"Filtered component_status keys: {list(component_status.keys())}")

        stored_schedule = None
        if not schedule_df.empty:
            with db_session:
                stored_schedule = store_schedule_new(schedule_df, component_status)

        scheduled_operations = []
        if not schedule_df.empty:
            with db_session:
                machine_details = {}
                for machine in Machine.select():
                    machine_name = f"{machine.work_center.code}-{machine.make}"
                    machine_details[machine.id] = {
                        'name': machine_name,
                        'id': machine.id
                    }

            # Convert schedule dataframe to response objects
            for _, row in schedule_df.iterrows():
                machine_id = row['machine_id']
                machine_name = machine_details.get(machine_id, {'name': f'Machine-{machine_id}'})['name']

                # Get production_order
                production_order = row.get('production_order')

                # Double-check that this is an active production order
                if production_order not in active_production_orders_set:
                    # print(f"Skipping operation for inactive production order: {production_order}")
                    continue

                # Get part description from mapping
                part_description = po_to_part_description_mapping.get(production_order, "")

                scheduled_operations.append(
                    ScheduledOperation(
                        component=row['partno'],
                        part_description=part_description,  # Add part description to the response
                        description=row['operation'],
                        machine=machine_name,
                        start_time=row['start_time'],
                        end_time=row['end_time'],
                        quantity=row['quantity'],
                        production_order=production_order
                    )
                )

        # Ensure correct types for overall_end_time
        if overall_end_time is None:
            overall_end_time = datetime.utcnow()

        # Convert daily_production from list to dict if needed
        if isinstance(daily_production, list):
            daily_production = {}

        # Always return work_centers_data, even if it's empty
        return ScheduleResponse(
            scheduled_operations=scheduled_operations,
            overall_end_time=overall_end_time,
            overall_time=str(overall_time),
            daily_production=daily_production,
            component_status=component_status,
            partially_completed=partially_completed,
            work_centers=work_centers_data  # Always return work centers data
        )

    except Exception as e:
        print(f"Error in schedule endpoint: {str(e)}")
        traceback.print_exc()  # Add this for full error details
        raise HTTPException(status_code=500, detail=str(e))


def store_schedule_new(schedule_df, component_status):
    """Store the generated schedule in the database, with simplified storage that skips versioning"""
    from app.models.scheduled import PlannedItem
    from app.models import Order, Operation, Machine
    try:
        stored_items = []

        for _, row in schedule_df.iterrows():
            part_no = row['partno']
            operation_desc = row['operation']
            machine_id = row['machine_id']
            production_order = row.get('production_order')

            # Find the specific order using both part number and production order
            order = Order.get(part_number=part_no, production_order=production_order)

            if not order:
                # print(f"No order found for part {part_no} with production order {production_order}")
                continue

            # Find the specific operation for this order and operation description
            operation = Operation.select(
                lambda op: op.order == order and op.operation_description == operation_desc
            ).first()

            if not operation:
                # print(f"No operation found for order {order.id} with description {operation_desc}")
                continue

            try:
                machine = Machine[machine_id]
            except ObjectNotFound:
                # print(f"Machine with ID {machine_id} not found")
                continue

            total_qty, current_qty, _ = extract_quantity(row['quantity'])

            # Get activation time from PartScheduleStatus (IST) for reference
            part_status = PartScheduleStatus.get(production_order=production_order)
            if not part_status:
                # print(f"No PartScheduleStatus found for production order {production_order}")
                continue
            ist_offset = timedelta(hours=5, minutes=30)
            activation_time = part_status.updated_at + ist_offset
            activation_time = activation_time.replace(second=0, microsecond=0)  # for comparison precision

            # CRITICAL FIX: Use the actual start_time from the schedule, not activation_time!
            # This preserves the sequential scheduling logic
            start_time = row['start_time'].to_pydatetime()
            end_time = row['end_time'].to_pydatetime()

            # print(f"Storing schedule for order {order.id}, operation {operation.id}: "
            #       f"Start: {start_time.strftime('%Y-%m-%d %H:%M:%S')}, "
            #       f"End: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")

            # Invalidate any existing schedule for this order/operation/machine/quantity with a different start_time
            existing_wrong_start = PlannedItem.select(
                lambda s: s.order == order and
                          s.operation == operation and
                          s.machine == machine and
                          s.total_quantity == total_qty and
                          s.initial_start_time != start_time
            )[:]
            for wrong_sched in existing_wrong_start:
                # print(
                #     f"Invalidating old schedule with wrong start_time for order {order.id}, operation {operation.id}, machine {machine.id}")
                wrong_sched.status = 'invalidated'

            # Check if this exact schedule already exists
            existing_schedule = PlannedItem.select(
                lambda s: s.order == order and
                          s.operation == operation and
                          s.machine == machine and
                          s.initial_start_time == start_time and
                          s.initial_end_time == end_time and
                          s.total_quantity == total_qty
            ).first()

            if existing_schedule:
                # print(
                #     f"Exact duplicate schedule found for order {order.id}, operation {operation.id}, machine {machine.id}")
                stored_items.append({
                    'schedule_item_id': existing_schedule.id,
                    'total_quantity': total_qty,
                    'current_quantity': current_qty,
                    'status': 'existing'
                })
                continue

            # Create new schedule item with the ACTUAL start_time from scheduling (not activation_time)
            schedule_item = PlannedItem(
                order=order,
                operation=operation,
                machine=machine,
                initial_start_time=start_time,  # Use the sequential start_time!
                initial_end_time=end_time,
                total_quantity=total_qty,
                remaining_quantity=total_qty - current_qty,
                status='scheduled',
                current_version=1  # Keep this for compatibility but won't use versions
            )

            stored_items.append({
                'schedule_item_id': schedule_item.id,
                'total_quantity': total_qty,
                'current_quantity': current_qty,
                'status': 'new'
            })

        return stored_items

    except Exception as e:
        print(f"Error storing schedule: {str(e)}")
        traceback.print_exc()
        raise e


@router.get("/schedule-batch/", response_model=ScheduleResponse)
async def schedule():
    """Generate schedule for active parts and store in database"""
    try:
        # Initialize work_centers_data at the start
        work_centers_data = []

        # Always fetch work centers data regardless of active production orders
        with db_session:
            # Fetch work centers and their machines for the response
            for work_center in WorkCenter.select():
                machines_in_wc = []
                for machine in work_center.machines:
                    machines_in_wc.append({
                        "id": str(machine.id),
                        "name": machine.make,
                        "model": machine.model,
                        "type": machine.type
                    })

                work_centers_data.append(
                    WorkCenterMachine(
                        work_center_code=work_center.code,
                        work_center_name=work_center.work_center_name or "",
                        machines=machines_in_wc,
                        is_schedulable=work_center.is_schedulable  # Include the flag in response
                    )
                )

            # Log schedulable work centers
            schedulable_work_centers = [wc for wc in WorkCenter.select() if wc.is_schedulable]
            # print(f"Schedulable work centers: {[wc.code for wc in schedulable_work_centers]}")

            # Get list of schedulable work center IDs for filtering
            schedulable_work_center_ids = {wc.id for wc in schedulable_work_centers}

            # Fetch all production orders with active status
            active_production_orders = select(p.production_order for p in PartScheduleStatus if p.status == 'active')[:]

            # Convert to a set for faster lookups
            active_production_orders_set = set(active_production_orders)

            # print(f"Active production orders: {active_production_orders_set}")

            if not active_production_orders_set:
                # print("No active production orders found")
                return ScheduleResponse(
                    scheduled_operations=[],
                    overall_end_time=datetime.utcnow(),
                    overall_time="0",
                    daily_production={},
                    component_status={},
                    partially_completed=["No parts are marked as active for scheduling"],
                    work_centers=work_centers_data  # Return work centers even if no active orders
                )

            # Get mapping of production orders to part numbers, descriptions, and required quantities
            po_to_part_mapping = {}
            po_to_part_description_mapping = {}  # New mapping for part descriptions
            part_po_to_quantity = {}

            # Get all active part statuses with their required quantities
            active_part_statuses = select(p for p in PartScheduleStatus if p.status == 'active')[:]

            for part_status in active_part_statuses:
                po = part_status.production_order
                part_number = part_status.part_number
                po_to_part_mapping[po] = part_number

                # Get quantity and part description from Order if possible
                order = Order.get(production_order=po, part_number=part_number)
                quantity = order.launched_quantity if order else 0

                # Store the part description in the mapping
                if order and order.part_description:
                    po_to_part_description_mapping[po] = order.part_description
                else:
                    po_to_part_description_mapping[po] = ""  # Empty string if no description

                # If no quantity found in Order, use a default value
                if quantity <= 0:
                    # Try to find the associated Order and get its launched_quantity
                    order = Order.get(part_number=part_number)
                    quantity = order.launched_quantity if order else 10  # Default to 10 if no quantity found

                    # Try to get description from this order as well
                    if order and order.part_description and po not in po_to_part_description_mapping:
                        po_to_part_description_mapping[po] = order.part_description

                # Store the part-PO specific quantity
                part_po_to_quantity[(part_number, po)] = quantity

        # Fetch operations for all parts
        df = fetch_operations()

        if df.empty:
            print("No operations found in fetch_operations()")
            return ScheduleResponse(
                scheduled_operations=[],
                overall_end_time=datetime.utcnow(),
                overall_time="0",
                daily_production={},
                component_status={},
                partially_completed=["No operations found in database"],
                work_centers=work_centers_data  # Return work centers even if no operations
            )

        # print(f"Original operations dataframe shape: {df.shape}")
        # print(f"Columns in operations dataframe: {df.columns.tolist()}")

        # Add production_order column to dataframe
        if 'production_order' not in df.columns:
            # Maps part numbers to their active production orders
            part_to_pos = {}
            for part_number in df['partno'].unique():
                part_to_pos[part_number] = []
                for po in active_production_orders_set:
                    if po_to_part_mapping.get(po) == part_number:
                        part_to_pos[part_number].append(po)

            # Expand the dataframe to include all active production orders
            # Filter for operations from schedulable work centers and expand the dataframe
            expanded_rows = []
            for (part_number, po), quantity in part_po_to_quantity.items():
                # Query actual operations for this specific (part_number, production_order)
                # CRITICAL FIX: Only include operations from work centers that are marked as schedulable
                matching_ops = Operation.select(
                    lambda o: o.order.part_number == part_number and
                              o.order.production_order == po and
                              o.work_center.is_schedulable == True  # Explicit check for is_schedulable=True
                )

                for op in matching_ops:
                    expanded_rows.append({
                        'partno': part_number,
                        'operation': op.operation_description,
                        'machine_id': op.machine.id,
                        'sequence': op.operation_number,
                        'time': float(op.ideal_cycle_time),
                        'production_order': po,
                        'work_center_id': op.work_center.id  # Add work center ID for filtering
                    })

            if expanded_rows:
                df = pd.DataFrame(expanded_rows)
            else:
                df = pd.DataFrame()  # Empty dataframe if no active production orders found

        # Double check if we have any operations for active production orders
        if df.empty:
            # print("No operations left after filtering for active production orders and schedulable work centers")
            return ScheduleResponse(
                scheduled_operations=[],
                overall_end_time=datetime.utcnow(),
                overall_time="0",
                daily_production={},
                component_status={},
                partially_completed=["No operations found for active production orders in schedulable work centers"],
                work_centers=work_centers_data  # Return work centers even if no operations for active orders
            )

        # Filter to keep only rows with active production orders
        df = df[df['production_order'].isin(active_production_orders_set)]

        # ADDITIONAL FILTER: Ensure all operations are from schedulable work centers
        # Create a mapping from machine_id to work_center_id
        machine_to_wc = {}
        with db_session:
            for machine in Machine.select():
                machine_to_wc[machine.id] = machine.work_center.id

        # Add a column with work center ID for each operation based on its machine
        df['work_center_id'] = df['machine_id'].map(machine_to_wc)

        # Filter out operations from non-schedulable work centers
        df = df[df['work_center_id'].isin(schedulable_work_center_ids)]

        if df.empty:
            print("No operations left after filtering for schedulable work centers")
            return ScheduleResponse(
                scheduled_operations=[],
                overall_end_time=datetime.utcnow(),
                overall_time="0",
                daily_production={},
                component_status={},
                partially_completed=["All operations are in non-schedulable work centers"],
                work_centers=work_centers_data
            )

        # Get the active part numbers based on the filtered dataframe
        active_part_numbers_in_df = df['partno'].unique().tolist()
        # print(f"Active part numbers in filtered dataframe: {active_part_numbers_in_df}")

        # Create component_quantities dictionary with the correct format
        component_quantities = {}
        for _, row in df.iterrows():
            part_number = row['partno']
            production_order = row['production_order']
            key = (part_number, production_order)

            # Use the saved quantity for this part-PO combination
            if key not in component_quantities and key in part_po_to_quantity:
                component_quantities[key] = part_po_to_quantity[key]

        # print(f"Component quantities for scheduling: {component_quantities}")

        # Get lead times within an active db session
        with db_session:
            lead_times = fetch_lead_times()

        # Filter lead_times to only include parts in filtered operations
        lead_times = {k: v for k, v in lead_times.items() if k in active_part_numbers_in_df}

        # Call scheduling algorithm with filtered dataframe and properly structured component_quantities
        with db_session:
            schedule_df, overall_end_time, overall_time, daily_production, \
                component_status, partially_completed = schedule_operations(
                df, component_quantities, lead_times
            )

        # Final verification
        if not schedule_df.empty:
            # print(f"Final schedule has {len(schedule_df)} operations")
            # print(f"Production orders in final schedule: {schedule_df['production_order'].unique().tolist()}")

            # Verify all production orders in the schedule are active
            scheduled_pos = set(schedule_df['production_order'].unique())
            invalid_pos = scheduled_pos - active_production_orders_set
            if invalid_pos:
                print(f"WARNING: Found inactive production orders in schedule: {invalid_pos}")
                # Filter out any operations with inactive production orders
                schedule_df = schedule_df[schedule_df['production_order'].isin(active_production_orders_set)]

        # Filter component_status to only include entries with active production orders
        filtered_component_status = {}
        for key, status in component_status.items():
            production_order = None

            # Check if this is a combined key (partno_production_order)
            if '_' in key:
                partno, production_order = key.split('_', 1)
            else:
                production_order = status.get('production_order')

            # Only include if the production_order is active
            if production_order in active_production_orders_set:
                filtered_component_status[key] = status

        # Replace the original component_status with filtered version
        component_status = filtered_component_status
        # print(f"Filtered component_status keys: {list(component_status.keys())}")

        stored_schedule = None
        if not schedule_df.empty:
            with db_session:
                stored_schedule = store_schedule(schedule_df, component_status)

        scheduled_operations = []
        if not schedule_df.empty:
            with db_session:
                machine_details = {}
                for machine in Machine.select():
                    machine_name = f"{machine.work_center.code}-{machine.make}"
                    machine_details[machine.id] = {
                        'name': machine_name,
                        'id': machine.id
                    }

            # Convert schedule dataframe to response objects
            for _, row in schedule_df.iterrows():
                machine_id = row['machine_id']
                machine_name = machine_details.get(machine_id, {'name': f'Machine-{machine_id}'})['name']

                # Get production_order
                production_order = row.get('production_order')

                # Double-check that this is an active production order
                if production_order not in active_production_orders_set:
                    # print(f"Skipping operation for inactive production order: {production_order}")
                    continue

                # Get part description from mapping
                part_description = po_to_part_description_mapping.get(production_order, "")

                scheduled_operations.append(
                    ScheduledOperation(
                        component=row['partno'],
                        part_description=part_description,  # Add part description to the response
                        description=row['operation'],
                        machine=machine_name,
                        start_time=row['start_time'],
                        end_time=row['end_time'],
                        quantity=row['quantity'],
                        production_order=production_order
                    )
                )

        # # Compute and store PDC from scheduled operations (skip default machines)
        # try:
        #     part_production_end_times = {}
        #     data_sources = {}

        #     for op in scheduled_operations:
        #         # Skip default machines
        #         if isinstance(op.machine, str) and 'default' in op.machine.lower():
        #             continue

        #         part_number = op.component
        #         production_order = op.production_order
        #         end_time = op.end_time

        #         if not all([part_number, production_order, end_time]):
        #             continue

        #         key = (part_number, production_order)
        #         if key not in part_production_end_times or end_time > part_production_end_times[key]:
        #             part_production_end_times[key] = end_time
        #             data_sources[key] = 'scheduled'

        #     if part_production_end_times:
        #         with db_session:
        #             for (part_number, production_order), pdc_data in part_production_end_times.items():
        #                 order = Order.get(production_order=production_order)
        #                 if not order:
        #                     continue
        #                 # If a PDC already exists for this part/PO, update it; else create new
        #                 existing = get_pdc_by_part_number_and_po(part_number, production_order)
        #                 if existing:
        #                     update_pdc_record(existing[0].id, pdc_data=pdc_data, updated_at=datetime.utcnow())
        #                 else:
        #                     create_pdc_record(
        #                         order_id=order.id,
        #                         part_number=part_number,
        #                         production_order=production_order,
        #                         pdc_data=pdc_data,
        #                         data_source=data_sources.get((part_number, production_order), 'scheduled'),
        #                         is_active=True
        #                     )
        # except Exception as e:
        #     print(f"Error computing/storing PDC in schedule endpoint: {str(e)}")

        # Ensure correct types for overall_end_time
        if overall_end_time is None:
            overall_end_time = datetime.utcnow()

        # Convert daily_production from list to dict if needed
        if isinstance(daily_production, list):
            daily_production = {}

        # Always return work_centers_data, even if it's empty
        return ScheduleResponse(
            scheduled_operations=scheduled_operations,
            overall_end_time=overall_end_time,
            overall_time=str(overall_time),
            daily_production=daily_production,
            component_status=component_status,
            partially_completed=partially_completed,
            work_centers=work_centers_data  # Always return work centers data
        )

    except Exception as e:
        print(f"Error in schedule endpoint: {str(e)}")
        traceback.print_exc()  # Add this for full error details
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/actual-production/", response_model=ProductionLogsResponse)
async def get_production_logs():
    """Retrieve aggregated production logs with related information"""
    try:
        with db_session:
            logs_query = select((
                                    log,
                                    log.operator,
                                    log.schedule_version,
                                    log.schedule_version.schedule_item,
                                    log.schedule_version.schedule_item.machine,
                                    log.schedule_version.schedule_item.operation,
                                    log.schedule_version.schedule_item.order
                                ) for log in ProductionLog)

            # Dictionary to store aggregated logs
            aggregated_logs = {}

            for (log, operator, version, schedule_item, machine, operation, order) in logs_query:
                # Create a unique key for grouping logs
                group_key = (
                    order.part_number if order else None,
                    operation.operation_description if operation else None,
                    machine.work_center.code + "-" + machine.make if machine and hasattr(machine,
                                                                                         'work_center') else None,
                    version.version_number if version else None
                )

                # Handle setup entries (quantity = 1) separately
                is_setup = log.quantity_completed == 1

                if is_setup:
                    # Create a separate entry for setup
                    log_entry = ProductionLogResponse(
                        id=log.id,
                        operator_id=operator.id,
                        start_time=log.start_time if hasattr(log, 'start_time') else None,
                        end_time=log.end_time if hasattr(log, 'end_time') else None,
                        quantity_completed=log.quantity_completed,
                        quantity_rejected=log.quantity_rejected,
                        part_number=order.part_number if order else None,
                        operation_description=operation.operation_description if operation else None,
                        machine_name=f"{machine.work_center.code}-{machine.make}" if machine and hasattr(machine,
                                                                                                         'work_center') else None,
                        notes="Setup " + (log.notes if hasattr(log, 'notes') else ""),
                        version_number=version.version_number if version else None
                    )
                    aggregated_logs[f"setup_{log.id}"] = log_entry
                else:
                    # Aggregate non-setup entries
                    if group_key in aggregated_logs:
                        existing = aggregated_logs[group_key]
                        # Update start_time to earliest
                        if log.start_time and (not existing.start_time or log.start_time < existing.start_time):
                            existing.start_time = log.start_time
                        # Update end_time to latest
                        if log.end_time and (not existing.end_time or log.end_time > existing.end_time):
                            existing.end_time = log.end_time
                        existing.quantity_completed += log.quantity_completed
                        existing.quantity_rejected += log.quantity_rejected
                    else:
                        aggregated_logs[group_key] = ProductionLogResponse(
                            id=log.id,
                            operator_id=operator.id,
                            start_time=log.start_time if hasattr(log, 'start_time') else None,
                            end_time=log.end_time if hasattr(log, 'end_time') else None,
                            quantity_completed=log.quantity_completed,
                            quantity_rejected=log.quantity_rejected,
                            part_number=order.part_number if order else None,
                            operation_description=operation.operation_description if operation else None,
                            machine_name=f"{machine.work_center.code}-{machine.make}" if machine and hasattr(machine,
                                                                                                             'work_center') else None,
                            notes=log.notes if hasattr(log, 'notes') else None,
                            version_number=version.version_number if version else None
                        )

            # Convert aggregated logs to list
            logs_data = list(aggregated_logs.values())

            # Calculate totals
            total_completed = sum(log.quantity_completed for log in logs_data)
            total_rejected = sum(log.quantity_rejected for log in logs_data)

            return ProductionLogsResponse(
                production_logs=logs_data,
                total_completed=total_completed,
                total_rejected=total_rejected,
                total_logs=len(logs_data)
            )

    except Exception as e:
        print(f"Error in production logs endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/combined-production/", response_model=ProductionLogsResponse)
async def get_combined_production_logs():
    """Retrieve combined production logs (setup + operation) with related information"""
    try:
        with db_session:
            logs_query = select((
                                    log,
                                    log.operator,
                                    log.schedule_version,
                                    log.schedule_version.schedule_item,
                                    log.schedule_version.schedule_item.machine,
                                    log.schedule_version.schedule_item.operation,
                                    log.schedule_version.schedule_item.order
                                ) for log in ProductionLog)

            # Dictionary to store combined logs
            combined_logs = {}

            for (log, operator, version, schedule_item, machine, operation, order) in logs_query:
                # Skip logs with null end_time
                if log.end_time is None:
                    continue

                # Create a unique key for grouping logs
                group_key = (
                    order.part_number if order else None,
                    operation.operation_description if operation else None,
                    machine.work_center.code + "-" + machine.make if machine and hasattr(machine,
                                                                                         'work_center') else None,
                    version.version_number if version else None
                )

                is_setup = log.quantity_completed == 1
                machine_name = f"{machine.work_center.code}-{machine.make}" if machine and hasattr(machine,
                                                                                                   'work_center') else None

                if group_key not in combined_logs:
                    combined_logs[group_key] = {
                        'setup': None,
                        'operation': None
                    }

                if is_setup:
                    combined_logs[group_key]['setup'] = {
                        'id': log.id,
                        'start_time': log.start_time,
                        'notes': log.notes
                    }
                else:
                    combined_logs[group_key]['operation'] = {
                        'id': log.id,
                        'end_time': log.end_time,
                        'quantity_completed': log.quantity_completed,
                        'quantity_rejected': log.quantity_rejected,
                        'operator_id': operator.id,
                        'part_number': order.part_number if order else None,
                        'operation_description': operation.operation_description if operation else None,
                        'machine_name': machine_name,
                        'version_number': version.version_number if version else None,
                        'notes': log.notes
                    }

            # Combine setup and operation data
            logs_data = []
            total_completed = 0
            total_rejected = 0

            for group_data in combined_logs.values():
                setup = group_data['setup']
                operation = group_data['operation']

                if setup and operation:
                    combined_entry = ProductionLogResponse(
                        id=operation['id'],
                        operator_id=operation['operator_id'],
                        start_time=setup['start_time'],
                        end_time=operation['end_time'],
                        quantity_completed=operation['quantity_completed'],
                        quantity_rejected=operation['quantity_rejected'],
                        part_number=operation['part_number'],
                        operation_description=operation['operation_description'],
                        machine_name=operation['machine_name'],
                        notes=f"Setup: {setup['notes']} | Operation: {operation['notes']}",
                        version_number=operation['version_number']
                    )
                    logs_data.append(combined_entry)
                    total_completed += operation['quantity_completed']
                    total_rejected += operation['quantity_rejected']

            return ProductionLogsResponse(
                production_logs=logs_data,
                total_completed=total_completed,
                total_rejected=total_rejected,
                total_logs=len(logs_data)
            )

    except Exception as e:
        print(f"Error in combined production logs endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/actual-planned-schedule/", response_model=CombinedScheduleProductionResponse)
async def get_combined_schedule_production():
    """Retrieve combined production logs with schedule batch information"""
    try:
        with db_session:
            # Get production logs
            logs_query = select((
                                    log,
                                    log.operator,
                                    log.schedule_version,
                                    log.schedule_version.schedule_item,
                                    log.schedule_version.schedule_item.machine,
                                    log.schedule_version.schedule_item.operation,
                                    log.schedule_version.schedule_item.order
                                ) for log in ProductionLog)

            # Dictionary to store combined logs
            combined_logs = {}

            for (log, operator, version, schedule_item, machine, operation, order) in logs_query:
                # Skip logs with null end_time
                if log.end_time is None:
                    continue

                group_key = (
                    order.part_number if order else None,
                    operation.operation_description if operation else None,
                    machine.work_center.code + "-" + machine.make if machine and hasattr(machine,
                                                                                         'work_center') else None,
                    version.version_number if version else None
                )

                is_setup = log.quantity_completed == 1
                machine_name = f"{machine.work_center.code}-{machine.make}" if machine and hasattr(machine,
                                                                                                   'work_center') else None

                if group_key not in combined_logs:
                    combined_logs[group_key] = {
                        'setup': None,
                        'operation': None
                    }

                if is_setup:
                    combined_logs[group_key]['setup'] = {
                        'id': log.id,
                        'start_time': log.start_time,
                        'notes': log.notes
                    }
                else:
                    combined_logs[group_key]['operation'] = {
                        'id': log.id,
                        'end_time': log.end_time,
                        'quantity_completed': log.quantity_completed,
                        'quantity_rejected': log.quantity_rejected,
                        'operator_id': operator.id,
                        'part_number': order.part_number if order else None,
                        'operation_description': operation.operation_description if operation else None,
                        'machine_name': machine_name,
                        'version_number': version.version_number if version else None,
                        'notes': log.notes
                    }

            # Process production logs
            logs_data = []
            total_completed = 0
            total_rejected = 0

            for group_data in combined_logs.values():
                setup = group_data['setup']
                operation = group_data['operation']

                if setup and operation:
                    combined_entry = ProductionLogResponse(
                        id=operation['id'],
                        operator_id=operation['operator_id'],
                        start_time=setup['start_time'],
                        end_time=operation['end_time'],
                        quantity_completed=operation['quantity_completed'],
                        quantity_rejected=operation['quantity_rejected'],
                        part_number=operation['part_number'],
                        operation_description=operation['operation_description'],
                        machine_name=operation['machine_name'],
                        notes=f"Setup: {setup['notes']} | Operation: {operation['notes']}",
                        version_number=operation['version_number']
                    )
                    logs_data.append(combined_entry)
                    total_completed += operation['quantity_completed']
                    total_rejected += operation['quantity_rejected']

            # Get schedule data
            with db_session:
                df = fetch_operations()
                component_quantities = fetch_component_quantities()
                lead_times = fetch_lead_times()

                schedule_df, overall_end_time, overall_time, daily_production, _, _ = schedule_operations(
                    df, component_quantities, lead_times
                )

            # Dictionary to store combined schedule operations
            combined_schedule = {}

            if not schedule_df.empty:
                machine_details = {
                    machine.id: f"{machine.work_center.code}-{machine.make}"
                    for machine in Machine.select()
                }

                orders_map = {
                    order.part_number: order.production_order
                    for order in Order.select()
                }

                for _, row in schedule_df.iterrows():
                    total_qty, current_qty, today_qty = extract_quantity(row['quantity'])

                    # Create key for grouping schedule operations
                    schedule_key = (
                        row['partno'],
                        row['operation'],
                        machine_details.get(row['machine_id'], f"Machine-{row['machine_id']}"),
                        orders_map.get(row['partno'], '')
                    )

                    is_setup = total_qty == 1

                    if is_setup:
                        if schedule_key not in combined_schedule:
                            combined_schedule[schedule_key] = {
                                'setup_start': row['start_time'],
                                'setup_end': row['end_time'],
                                'operation_end': None,
                                'total_qty': 0,
                                'current_qty': 0,
                                'today_qty': 0
                            }
                    else:
                        if schedule_key in combined_schedule:
                            combined_schedule[schedule_key]['operation_end'] = row['end_time']
                            combined_schedule[schedule_key]['total_qty'] = max(
                                combined_schedule[schedule_key]['total_qty'], total_qty)
                            combined_schedule[schedule_key]['current_qty'] = max(
                                combined_schedule[schedule_key]['current_qty'], current_qty)
                            combined_schedule[schedule_key]['today_qty'] = max(
                                combined_schedule[schedule_key]['today_qty'], today_qty)
                        else:
                            combined_schedule[schedule_key] = {
                                'setup_start': row['start_time'],
                                'setup_end': row['end_time'],
                                'operation_end': row['end_time'],
                                'total_qty': total_qty,
                                'current_qty': current_qty,
                                'today_qty': today_qty
                            }

            scheduled_operations = []

            for (component, description, machine, production_order), data in combined_schedule.items():
                if data['operation_end']:  # Only include completed operations
                    quantity_str = f"Process({data['current_qty']}/{data['total_qty']}pcs, Today: {data['today_qty']}pcs)"
                    scheduled_operations.append(
                        ScheduledOperation(
                            component=component,
                            description=description,
                            machine=machine,
                            start_time=data['setup_start'],
                            end_time=data['operation_end'],
                            quantity=quantity_str,
                            production_order=production_order
                        )
                    )

            return CombinedScheduleProductionResponse(
                production_logs=logs_data,
                total_completed=total_completed,
                total_rejected=total_rejected,
                total_logs=len(logs_data),
                scheduled_operations=scheduled_operations,
                overall_end_time=overall_end_time,
                overall_time=str(overall_time),
                daily_production=daily_production
            )

    except Exception as e:
        print(f"Error in combined schedule production endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/part-production-timeline/", response_model=PartProductionResponse)
async def get_part_production_timeline():
    """Retrieve the production timeline for each part number using schedule_versions table.
    Only returns parts that are marked as active in PartScheduleStatus."""
    try:
        with db_session:
            # First get all active part numbers from PartScheduleStatus
            active_parts = select(p.part_number for p in PartScheduleStatus if p.status == 'active')[:]

            # If no active parts found, return empty response
            if not active_parts:
                return PartProductionResponse(
                    items=[],
                    total_parts=0
                )

            # Get planned schedule items with related data, filtered by active parts (no version tracking)
            items_query = select((
                                        item,
                                        item.order,
                                        item.operation,
                                        item.machine
                                    ) for item in PlannedScheduleItem
                                    if item.order.part_number in active_parts and item.schedule_history is not None)

            # Dictionary to store all operations by part number and production order
            part_operations = defaultdict(list)

            # Group operations by part number and production order
            for (schedule_item, order, operation, machine) in items_query:
                # Use a composite key of part_number and production_order
                key = (order.part_number, order.production_order)

                # Extract the proper quantity from the planned item
                total_qty = schedule_item.total_quantity

                # In case the quantity is still 1, try to get a more accurate quantity
                if total_qty == 1:
                    # Query for a better quantity value from related operations
                    order_operations = Operation.select(lambda op: op.order == order)
                    if order_operations:
                        # Look for the operation with the highest quantity as the true quantity
                        max_qty = max(
                            (op.quantity for op in order_operations if hasattr(op, 'quantity') and op.quantity),
                            default=total_qty)
                        if max_qty > total_qty:
                            total_qty = max_qty

                part_operations[key].append({
                    'operation_description': operation.operation_description,
                    'operation_number': operation.operation_number if hasattr(operation, 'operation_number') else 0,
                    'start_time': schedule_item.initial_start_time,
                    'end_time': schedule_item.initial_end_time,
                    'total_quantity': total_qty,
                    'remaining_quantity': schedule_item.remaining_quantity,
                    'completed_quantity': max(0, (schedule_item.total_quantity or 0) - (schedule_item.remaining_quantity or 0)),
                    'status': schedule_item.status,
                    'production_order': order.production_order
                })

            # Process results
            results = []
            for (part_number, production_order), operations in part_operations.items():
                # If there's an operation_number attribute, sort by that
                # Otherwise, sort by start_time to determine first and last
                try:
                    operations.sort(key=lambda x: x['operation_number'])
                except:
                    operations.sort(key=lambda x: x['start_time'])

                # Get the max quantity from all operations for this part number
                max_quantity = max(op['total_quantity'] for op in operations)

                # Use the order quantity where available, or fall back to the highest operation quantity
                with db_session:
                    # Use select with a WHERE clause for the specific production order
                    orders = select(
                        o for o in Order if o.part_number == part_number and o.production_order == production_order)[:]

                    # There should be exactly one order now
                    if orders:
                        order = orders[0]
                        order_quantity = order.quantity if hasattr(order, 'quantity') else max_quantity
                    else:
                        order_quantity = max_quantity

                # Use the higher of the two quantities
                total_quantity = max(max_quantity, order_quantity)

                # If we still have quantity = 1, try to get quantity from the extract_quantity function
                if total_quantity == 1:
                    try:
                        for op in operations:
                            if hasattr(op, 'quantity_str'):
                                total_qty, _, _ = extract_quantity(op['quantity_str'])
                                if total_qty > total_quantity:
                                    total_quantity = total_qty
                    except:
                        pass

                # Sum the completed quantities across all operations
                total_completed = sum(op['completed_quantity'] for op in operations)

                # For remaining quantity, take the sum of remaining quantities or calculate from the ratio
                total_remaining = sum(op['remaining_quantity'] for op in operations)

                # Use status from the last operation
                status = operations[-1]['status']

                unique_operation_numbers = set(op['operation_number'] for op in operations)

                # print('^^^'*50)
                # print(operations)
                # print('^^^'*50)

                results.append(PartProductionTimeline(
                    part_number=part_number,
                    production_order=production_order,
                    completed_total_quantity=total_quantity,
                    operations_count=len(unique_operation_numbers),
                    status=status
                ))

            # Sort by part number alphabetically
            # Filter only scheduled statuses
            filtered_results = [item for item in results if item.status == "scheduled"]

            # Sort by part number alphabetically
            filtered_results.sort(key=lambda x: x.part_number)

            return PartProductionResponse(
                items=filtered_results,
                total_parts=len(filtered_results)
            )                                                                                                                                                                                                                                                                                                                                                                                                                                   


    except Exception as e:
        print(f"Error retrieving part production timeline: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


def check_machine_status(machine_id: int, time: datetime) -> Tuple[bool, datetime]:
    """
    Check if a machine is available at a given time.
    Returns (is_available, next_possible_time).
    If not available and we know when it becomes available, next_possible_time is that datetime;
    otherwise next_possible_time is None.
    """
    with db_session:
        # Get the machine status that's active at the given time
        machine_status = select(ms for ms in MachineStatus
                                if ms.machine.id == machine_id
                                and ms.available_from != None
                                and ms.available_from <= time
                                and (ms.available_to is None or ms.available_to > time)).first()

        # If no status record, assume machine is available
        if not machine_status:
            return True, time

        # If status ID is 2 (OFF/inactive), machine is not available
        if machine_status.status.id == 2:
            if machine_status.available_to:
                return False, machine_status.available_to
            return False, None

        # For all other statuses (ID 1 - ON), check if we're after available_from
        return True, time


@router.get("/machine-utilization", response_model=List[MachineUtilization])
@db_session
def get_machine_utilization(
        month: Optional[int] = Query(None, description="Month (1-12)"),
        year: Optional[int] = Query(None, description="Year (YYYY)"),
        machine_id: Optional[int] = Query(None, description="Filter by specific machine ID")
):
    """
    Get machine utilization metrics.

    Calculates:
    - Available hours: working hours (16) * working days in month * 0.85 (efficiency)
    - Utilized hours: Sum of scheduled time from planned schedule items for active production orders, capped at available hours
    - Remaining hours: Available - Utilized
    """
    # Default to current month/year if not specified
    if not month or not year:
        current_date = datetime.now()
        month = month or current_date.month
        year = year or current_date.year

    # Validate inputs
    if not 1 <= month <= 12:
        raise HTTPException(status_code=400, detail="Month must be between 1 and 12")

    # print(f"\n=== MACHINE UTILIZATION DEBUG (Month: {month}/{year}) ===")

    # Calculate working days in the month (excluding Sundays only)
    _, days_in_month = calendar.monthrange(year, month)
    working_days = 0
    for day in range(1, days_in_month + 1):
        weekday = datetime(year, month, day).weekday()
        # 0-5 are Monday to Saturday (working days), 6 is Sunday
        if weekday < 6:
            working_days += 1

    # print(f"Working days in {month}/{year}: {working_days}")

    # Calculate available hours
    # Formula: working hours (8) * working days in month * 0.85 (efficiency)
    efficiency_factor = 0.85
    daily_working_hours = 16

    # Monthly calculation based on working days only
    available_hours = working_days * daily_working_hours * efficiency_factor
    # print(
    #     f"Base available hours: {working_days} days Ãƒâ€" {daily_working_hours} hours Ãƒâ€" {efficiency_factor} = {available_hours}")

    # Set date range for the month
    start_date = datetime(year, month, 1)
    if month == 12:
        end_date = datetime(year + 1, 1, 1)
    else:
        end_date = datetime(year, month + 1, 1)

    # print(f"Date range: {start_date} to {end_date}")

    # Get all active production orders first
    active_production_orders = select(ps.production_order for ps in PartScheduleStatus if ps.status == 'active')[:]
    # print(f"Active production orders: {len(active_production_orders)}")

    # Query to fetch machines
    machines_query = select(m for m in Machine)
    if machine_id:
        machines_query = machines_query.filter(lambda m: m.id == machine_id)

    machines = machines_query[:]
    # print(f"Processing {len(machines)} machines")

    # Pre-fetch all machine status records for the date range to avoid multiple database queries
    machine_statuses = select(ms for ms in MachineStatus
                              if ms.available_from is not None
                              and (
                                  ms.available_from < end_date and 
                                  (ms.available_to is None or ms.available_to > start_date)
                              ))[:]

    # print(f"Found {len(machine_statuses)} machine status records in date range")

    # Group machine statuses by machine ID for faster lookup
    machine_status_map = {}
    for ms in machine_statuses:
        if ms.machine.id not in machine_status_map:
            machine_status_map[ms.machine.id] = []
        machine_status_map[ms.machine.id].append(ms)

    # Sort statuses by available_from for each machine
    for machine_id in machine_status_map:
        machine_status_map[machine_id].sort(key=lambda x: x.available_from)

    # Pre-fetch all schedule items for the date range to avoid multiple database queries
    all_schedule_items = select(p for p in PlannedScheduleItem
                                if p.order.production_order in active_production_orders
                                and ((p.initial_start_time >= start_date and p.initial_start_time < end_date) or
                                     (p.initial_end_time > start_date and p.initial_end_time <= end_date) or
                                     (p.initial_start_time <= start_date and p.initial_end_time >= end_date)))[:]

    # print(f"Found {len(all_schedule_items)} schedule items in date range")

    # Group schedule items by machine ID for faster lookup
    schedule_items_map = {}
    for item in all_schedule_items:
        if item.machine.id not in schedule_items_map:
            schedule_items_map[item.machine.id] = []
        schedule_items_map[item.machine.id].append(item)

    result = []
    for machine in machines:
        # print(f"\n--- Processing Machine {machine.id} ({machine.make}) ---")

        # Get planned schedule items for this machine (from pre-fetched data)
        schedule_items = schedule_items_map.get(machine.id, [])
        # print(f"Machine {machine.id} has {len(schedule_items)} schedule items")

        # Calculate utilized hours from planned schedule items
        utilized_hours = 0
        # Track hours used per day to prevent counting more than daily_working_hours per day
        daily_hours = {}

        for item in schedule_items:
            # Handle cases where schedule item spans across months
            actual_start = max(item.initial_start_time, start_date)
            actual_end = min(item.initial_end_time, end_date)

            # Process each day within the schedule item separately - optimized
            current_day = actual_start.replace(hour=0, minute=0, second=0, microsecond=0)
            end_day = actual_end.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

            while current_day < end_day:
                day_key = current_day.strftime('%Y-%m-%d')

                # Initialize this day's hours if not already tracked
                if day_key not in daily_hours:
                    daily_hours[day_key] = 0

                # Calculate hours for this segment on this day
                segment_start = max(actual_start, current_day)
                segment_end = min(actual_end, current_day + timedelta(days=1))

                # Skip if segment end is before or equal to segment start
                if segment_end <= segment_start:
                    current_day += timedelta(days=1)
                    continue

                # Calculate duration of this segment on this day
                segment_hours = (segment_end - segment_start).total_seconds() / 3600

                # Only count up to the daily working hours limit
                available_for_day = daily_working_hours - daily_hours[day_key]
                if available_for_day > 0:
                    hours_to_add = min(segment_hours, available_for_day)
                    daily_hours[day_key] += hours_to_add
                    utilized_hours += hours_to_add

                current_day += timedelta(days=1)

        # print(f"Machine {machine.id} utilized hours: {utilized_hours}")

        # Ensure utilized hours don't exceed available hours
        utilized_hours = min(utilized_hours, available_hours)

        # Calculate remaining and utilization percentage
        remaining_hours = max(0, available_hours - utilized_hours)
        utilization_percentage = (utilized_hours / available_hours * 100) if available_hours > 0 else 0

        # Get the work center name from the related work center
        work_center_name = machine.work_center.work_center_name if machine.work_center else None

        # Calculate downtime hours using optimized approach
        downtime_hours = 0
        current_time = start_date

        # Get statuses for this machine
        machine_statuses_list = machine_status_map.get(machine.id, [])
        # print(f"Machine {machine.id} has {len(machine_statuses_list)} status records")

        # Process downtime periods more efficiently
        current_status = None
        status_index = 0

        while current_time < end_date:
            # Find the next status that applies
            while status_index < len(machine_statuses_list):
                status = machine_statuses_list[status_index]
                if status.available_from <= current_time:
                    if status.available_to is None or status.available_to > current_time:
                        current_status = status
                        break
                    else:
                        status_index += 1
                else:
                    break

            if current_status and current_status.status.id == 2:  # Machine is OFF
                # print(f"Machine {machine.id} is OFF at {current_time}")
                # print(f"Status record: from={current_status.available_from}, to={current_status.available_to}")

                # Machine is unavailable
                if current_status.available_to:
                    # Machine will be available again - check if downtime overlaps with user's range
                    actual_downtime_start = current_status.available_from
                    actual_downtime_end = current_status.available_to
                    
                    # Only calculate downtime if it overlaps with the user's date range
                    if actual_downtime_end > start_date and actual_downtime_start < end_date:
                        # Calculate the overlap between actual downtime and user's range
                        downtime_start = max(actual_downtime_start, start_date)
                        downtime_interval_end = min(actual_downtime_end, end_date)
                        hours_to_add = overlap_with_shift(downtime_start, downtime_interval_end, 6, 22)
                        downtime_hours += hours_to_add
                        # print(f"Actual downtime: {actual_downtime_start} to {actual_downtime_end}")
                        # print(f"Overlap with user range: {downtime_start} to {downtime_interval_end}")
                        # print(f"Will be available again at: {current_status.available_to}")
                        # print(f"Downtime hours added: {hours_to_add}")
                    else:
                        # print(f"Downtime period {actual_downtime_start} to {actual_downtime_end} does not overlap with user range {start_date} to {end_date}")
                        pass

                    current_time = min(actual_downtime_end, end_date)
                    # Move to next status if this one ends
                    if current_status.available_to <= current_time:
                        status_index += 1
                        current_status = None
                else:
                    # Machine is permanently unavailable - check if downtime overlaps with user's range
                    actual_downtime_start = current_status.available_from
                    
                    # Only calculate downtime if it overlaps with the user's date range
                    if actual_downtime_start < end_date:
                        # Calculate the overlap between actual downtime and user's range
                        downtime_start = max(actual_downtime_start, start_date)
                        downtime_interval_end = end_date
                        hours_to_add = overlap_with_shift(downtime_start, downtime_interval_end, 6, 22)
                        downtime_hours += hours_to_add
                        # print(f"Actual downtime: {actual_downtime_start} to permanent")
                        # print(f"Overlap with user range: {downtime_start} to {downtime_interval_end}")
                        # print(f"Machine permanently unavailable, adding {hours_to_add} hours")
                    else:
                        # print(f"Downtime period {actual_downtime_start} to permanent does not overlap with user range {start_date} to {end_date}")
                        pass
                    break
            else:
                # Machine is available, move to next hour
                current_time += timedelta(hours=1)

        # print(f"Machine {machine.id} total downtime hours: {downtime_hours}")

        # Subtract efficiency-adjusted downtime hours from available, then recalc metrics
        adjusted_downtime_hours = downtime_hours * efficiency_factor
        available_hours = max(0, available_hours - adjusted_downtime_hours)
        utilized_hours = min(utilized_hours, available_hours)
        remaining_hours = max(0, available_hours - utilized_hours)
        utilization_percentage = (utilized_hours / available_hours * 100) if available_hours > 0 else 0
        # print(
            # f"Final metrics for Machine {machine.id}: {downtime_hours} {available_hours} {utilized_hours} {remaining_hours}")

        result.append(MachineUtilization(
            machine_id=machine.id,
            machine_type=machine.type,
            machine_make=machine.make,
            machine_model=machine.model,
            work_center_name=work_center_name,
            work_center_bool=machine.work_center.is_schedulable,
            available_hours=round(available_hours, 2),
            utilized_hours=round(utilized_hours, 2),
            remaining_hours=round(remaining_hours, 2),
            utilization_percentage=round(utilization_percentage, 2)
        ))

    # print(f"\n=== END MACHINE UTILIZATION DEBUG ===")
    return result


@router.get("/machine-utilization/range", response_model=List[MachineUtilization])
@db_session
def get_machine_utilization_by_range(
        start_date: datetime = Query(..., description="Start date (YYYY-MM-DD)"),
        end_date: datetime = Query(..., description="End date (YYYY-MM-DD)"),
        machine_id: Optional[int] = Query(None, description="Filter by specific machine ID")
):
    """
    Get machine utilization metrics for a custom date range.

    Calculates:
    - Available hours: working hours (8) * working days in range * 0.85 (efficiency)
    - Utilized hours: Sum of scheduled time from planned schedule items for active production orders, capped at available hours
    - Remaining hours: Available - Utilized
    """
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="End date must be after start date")

    # print(f"\n=== MACHINE UTILIZATION RANGE DEBUG ===")
    # print(f"Date range: {start_date} to {end_date}")

    # Calculate working days in the range (excluding Sundays only) - optimized
    working_days = 0
    current_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    # Treat end_date as inclusive by using an exclusive upper bound at next midnight
    range_end = end_date.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
 
    # Optimize: Calculate working days more efficiently
    days_diff = (range_end - current_date).days
    if days_diff > 0:
        # Calculate full weeks
        full_weeks = days_diff // 7
        working_days = full_weeks * 6  # 6 working days per week (Monday to Saturday)

        # Calculate remaining days
        remaining_days = days_diff % 7
        for i in range(remaining_days):
            weekday = (current_date + timedelta(days=i)).weekday()
            if weekday < 6:  # Monday to Saturday
                working_days += 1

    # print(f"Working days in range: {working_days}")

    # Calculate available hours
    # Formula: working hours (15) * working days in range * 0.85 (efficiency)
    efficiency_factor = 0.85
    daily_working_hours = 16

    # Base available hours for the date range based on working days
    base_available_hours = working_days * daily_working_hours * efficiency_factor
    # print(
    #     f"Base available hours: {working_days} days Ãƒâ€" {daily_working_hours} hours Ãƒâ€" {efficiency_factor} = {base_available_hours}")

    # Get all active production orders first
    active_production_orders = select(ps.production_order for ps in PartScheduleStatus if ps.status == 'active')[:]
    # print(f"Active production orders: {len(active_production_orders)}")

    # Query to fetch machines
    machines_query = select(m for m in Machine)
    if machine_id:
        machines_query = machines_query.filter(lambda m: m.id == machine_id)

    machines = machines_query[:]
    # print(f"Processing {len(machines)} machines")

    # Pre-fetch all machine status records for the date range to avoid multiple database queries
    machine_statuses = select(ms for ms in MachineStatus
                              if ms.available_from is not None
                              and (
                                  ms.available_from < range_end and 
                                  (ms.available_to is None or ms.available_to > start_date)
                              ))[:]

    # print(f"Found {len(machine_statuses)} machine status records in date range")

    # Group machine statuses by machine ID for faster lookup
    machine_status_map = {}
    for ms in machine_statuses:
        if ms.machine.id not in machine_status_map:
            machine_status_map[ms.machine.id] = []
        machine_status_map[ms.machine.id].append(ms)

    # Sort statuses by available_from for each machine
    for machine_id in machine_status_map:
        machine_status_map[machine_id].sort(key=lambda x: x.available_from)

    # Pre-fetch all schedule items for the date range to avoid multiple database queries
    all_schedule_items = select(p for p in PlannedScheduleItem
                                if p.order.production_order in active_production_orders
                                and ((p.initial_start_time >= start_date and p.initial_start_time < range_end) or
                                     (p.initial_end_time > start_date and p.initial_end_time <= range_end) or
                                     (p.initial_start_time <= start_date and p.initial_end_time >= range_end)))[:]

    # print(f"Found {len(all_schedule_items)} schedule items in date range")

    # Group schedule items by machine ID for faster lookup
    schedule_items_map = {}
    for item in all_schedule_items:
        if item.machine.id not in schedule_items_map:
            schedule_items_map[item.machine.id] = []
        schedule_items_map[item.machine.id].append(item)

    result = []

    for machine in machines:
        # print(f"\n--- Processing Machine {machine.id} ({machine.make}) ---")

        # Start with base available hours for this machine
        available_hours = base_available_hours

        # Calculate downtime hours using optimized approach
        downtime_hours = 0
        current_time = start_date

        # Get statuses for this machine
        machine_statuses_list = machine_status_map.get(machine.id, [])
        # print(f"Machine {machine.id} has {len(machine_statuses_list)} status records")

        # Process downtime periods more efficiently
        downtime_periods = []
        current_status = None
        status_index = 0

        while current_time < range_end:
            # Early exit: If no more statuses to check and machine is available
            if status_index >= len(machine_statuses_list) and not current_status:
                break

            # Find the next status that applies
            while status_index < len(machine_statuses_list):
                status = machine_statuses_list[status_index]
                if status.available_from <= current_time:
                    if status.available_to is None or status.available_to > current_time:
                        current_status = status
                        break
                    else:
                        status_index += 1
                else:
                    break

            if current_status and current_status.status.id == 2:  # Machine is OFF
                # print(f"Machine {machine.id} is OFF at {current_time}")
                # print(f"Status record: from={current_status.available_from}, to={current_status.available_to}")

                # Machine is unavailable
                if current_status.available_to:
                    # Machine will be available again - check if downtime overlaps with user's range
                    actual_downtime_start = current_status.available_from
                    actual_downtime_end = current_status.available_to
                    
                    # Only calculate downtime if it overlaps with the user's date range
                    if actual_downtime_end > start_date and actual_downtime_start < range_end:
                        # Calculate the overlap between actual downtime and user's range
                        downtime_start = max(actual_downtime_start, start_date)
                        downtime_interval_end = min(actual_downtime_end, range_end)
                        hours_to_add = overlap_with_shift(downtime_start, downtime_interval_end, 6, 22)
                        downtime_hours += hours_to_add
                        # print(f"Actual downtime: {actual_downtime_start} to {actual_downtime_end}")
                        # print(f"Overlap with user range: {downtime_start} to {downtime_interval_end}")
                        # print(f"Will be available again at: {current_status.available_to}")
                        # print(f"Downtime hours added (with efficiency): {hours_to_add}")
                    else:
                        # print(f"Downtime period {actual_downtime_start} to {actual_downtime_end} does not overlap with user range {start_date} to {range_end}")
                        pass

                    current_time = min(actual_downtime_end, range_end)
                    # Move to next status if this one ends
                    if current_status.available_to <= current_time:
                        status_index += 1
                        current_status = None
                else:
                    # Machine is permanently unavailable - check if downtime overlaps with user's range
                    actual_downtime_start = current_status.available_from
                    
                    # Only calculate downtime if it overlaps with the user's date range
                    if actual_downtime_start < range_end:
                        # Calculate the overlap between actual downtime and user's range
                        downtime_start = max(actual_downtime_start, start_date)
                        downtime_interval_end = range_end
                        hours_to_add = overlap_with_shift(downtime_start, downtime_interval_end, 6, 22)
                        downtime_hours += hours_to_add
                        # print(f"Actual downtime: {actual_downtime_start} to {actual_downtime_end}")
                        # print(f"Overlap with user range: {downtime_start} to {downtime_interval_end}")
                        # print(f"Will be available again at: {current_status.available_to}")
                        # print(f"Downtime hours added (with efficiency): {hours_to_add}")
                    else:
                        # print(f"Downtime period {actual_downtime_start} to {actual_downtime_end} does not overlap with user range {start_date} to {range_end}")
                        pass
                    break
            else:
                # Machine is available, move to next hour
                current_time += timedelta(hours=1)

        # print(f"Machine {machine.id} total downtime hours: {downtime_hours}")

        # Get planned schedule items for this machine (from pre-fetched data)
        schedule_items = schedule_items_map.get(machine.id, [])
        # print(f"Machine {machine.id} has {len(schedule_items)} schedule items")

        # Calculate utilized hours from planned schedule items
        utilized_hours = 0
        daily_hours = {}

        for item in schedule_items:
            # Handle cases where schedule item spans across the date range boundaries
            actual_start = max(item.initial_start_time, start_date)
            actual_end = min(item.initial_end_time, range_end)

            # Process each day within the schedule item separately
            current_day = actual_start.replace(hour=0, minute=0, second=0, microsecond=0)
            end_day = actual_end.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

            while current_day < end_day:
                day_key = current_day.strftime('%Y-%m-%d')

                # Initialize this day's hours if not already tracked
                if day_key not in daily_hours:
                    daily_hours[day_key] = 0

                # Calculate hours for this segment on this day
                segment_start = max(actual_start, current_day)
                segment_end = min(actual_end, current_day + timedelta(days=1))

                # Skip if segment end is before or equal to segment start
                if segment_end <= segment_start:
                    current_day += timedelta(days=1)
                    continue

                # Calculate duration of this segment on this day
                segment_hours = (segment_end - segment_start).total_seconds() / 3600

                # Only count up to the daily working hours limit
                available_for_day = daily_working_hours - daily_hours[day_key]
                if available_for_day > 0:
                    hours_to_add = min(segment_hours, available_for_day)
                    daily_hours[day_key] += hours_to_add
                    utilized_hours += hours_to_add

                current_day += timedelta(days=1)

        # print(f"Machine {machine.id} utilized hours: {utilized_hours}")

        # Adjust for downtime (efficiency-adjusted) and calculate final metrics
        adjusted_downtime_hours = downtime_hours * efficiency_factor
        available_hours = max(0, base_available_hours - adjusted_downtime_hours)
        
        # If adjusted downtime exceeds available hours for the given range, return 0
        if base_available_hours < adjusted_downtime_hours:
            available_hours = 0
            utilized_hours = 0
            remaining_hours = 0
            utilization_percentage = 0
        else:
            utilized_hours = min(utilized_hours, available_hours)
            remaining_hours = max(0, available_hours - utilized_hours)
            utilization_percentage = (utilized_hours / available_hours * 100) if available_hours > 0 else 0
        # print(
        #      f"Final metrics for Machine {machine.id}: {downtime_hours} {available_hours} {utilized_hours} {remaining_hours}")

        # Get the work center name from the related work center
        work_center_name = machine.work_center.code if machine.work_center else None

        result.append(MachineUtilization(
            machine_id=machine.id,
            machine_type=machine.type,
            machine_make=machine.make,
            machine_model=machine.model,
            work_center_name=work_center_name,
            work_center_bool=machine.work_center.is_schedulable,
            available_hours=round(available_hours, 2),
            utilized_hours=round(utilized_hours, 2),
            remaining_hours=round(remaining_hours, 2),
            utilization_percentage=round(utilization_percentage, 2)
        ))

    # print(f"\n=== END MACHINE UTILIZATION RANGE DEBUG ===")
    return result

@router.post("/set-order-completion/{order_id}", response_model=OrderCompletionResponse)
async def set_order_completion(order_id: int, request: OrderCompletionRequest):
    """Set completion status for an order"""
    try:
        with db_session:
            order = Order.get(id=order_id)
            if not order:
                raise HTTPException(status_code=404, detail="Order not found")

            # Find or create tracking record
            tracking = OrderCompleted.get(order_id=order)
            if not tracking:
                tracking = OrderCompleted(
                    order_id=order,
                    is_completed=request.is_completed
                )
            else:
                tracking.is_completed = request.is_completed
                tracking.triggered_at = datetime.utcnow()

            # Update PartScheduleStatus to inactive when order is marked as complete
            if request.is_completed:
                part_schedule_status = PartScheduleStatus.get(production_order=order.production_order)
                if part_schedule_status:
                    part_schedule_status.status = 'inactive'
                    part_schedule_status.updated_at = datetime.utcnow()

            return OrderCompletionResponse(
                message=f"Order {order_id} completion status set to {request.is_completed}",
                triggered_at=tracking.triggered_at.isoformat(),
                order_id=order_id
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/order-completion-record/{order_id}", response_model=OrderCompletionStatus)
async def get_order_completion_status(order_id: int):
    """Get completion status for an order"""
    try:
        with db_session:
            order = Order.get(id=order_id)
            if not order:
                raise HTTPException(status_code=404, detail="Order not found")

            # Get project name
            project_name = order.project.name if order.project else "Unknown Project"
            # print(
            #     f"DEBUG: Order {order_id} - Project ID: {order.project.id if order.project else 'None'}, Project Name: {project_name}")

            # Get completion tracking
            tracking = OrderCompleted.get(order_id=order)

            # Calculate status and progress
            if tracking and tracking.is_completed:
                status = "Completed"
                progress = 100.0
                completion_date = tracking.triggered_at.isoformat()
                message = f"Order completed on {completion_date}"
            else:
                status = "In Progress"
                progress = 0.0
                completion_date = None
                message = "Order is currently in progress"

            return OrderCompletionStatus(
                order_id=order_id,
                production_order=order.production_order,
                part_number=order.part_number,
                project_name=project_name,
                status=status,
                progress=progress,
                completion_date=completion_date,
                message=message
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/all-completion-records", response_model=AllCompletionStatusResponse)
async def get_all_completion_status():
    """Get completion status for all orders"""
    try:
        with db_session:
            tracking_records = select(t for t in OrderCompleted)[:]

            completion_records = []
            for record in tracking_records:
                order = record.order_id
                project_name = order.project.name if order.project else "Unknown Project"
                # print(
                #     f"DEBUG: Order {order.id} - Project ID: {order.project.id if order.project else 'None'}, Project Name: {project_name}")

                # Calculate status and progress
                if record.is_completed:
                    status = "Completed"
                    progress = 100.0
                    completion_date = record.triggered_at.isoformat()
                    message = f"Order completed on {completion_date}"

                completion_records.append(
                    OrderCompletionRecord(
                        order_id=order.id,
                        production_order=order.production_order,
                        part_number=order.part_number,
                        project_name=project_name,
                        status=status,
                        progress=progress,
                        completion_date=completion_date,
                        message=message
                    )
                )

            return AllCompletionStatusResponse(completion_records=completion_records)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/check-order-completion/{part_number}/{production_order}")
async def check_order_completion(part_number: str, production_order: str):
    """Check if an order is complete based on part number and production order"""
    try:
        with db_session:
            # Find the order by part number and production order
            order = Order.get(part_number=part_number, production_order=production_order)
            if not order:
                raise HTTPException(
                    status_code=404,
                    detail=f"Order not found for part number {part_number} and production order {production_order}"
                )

            # Check if there's a completion tracking record
            tracking = OrderCompleted.get(order_id=order)

            # Return boolean indicating completion status
            is_completed = tracking.is_completed if tracking else False

            return {
                "part_number": part_number,
                "production_order": production_order,
                "is_completed": is_completed,
                "order_id": order.id
            }

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/schedule/latest", response_model=ScheduleResponse)
async def get_latest_schedule():
    """Fetch the latest schedule for the latest/active schedule history.
    - Determine current history (is_active=True, else highest version)
    - Filter PlannedScheduleItem by that history
    - Take highest ScheduleVersion per item
    Returns the same response shape as schedule-batch."""
    try:
        work_centers_data = []
        scheduled_operations: List[ScheduledOperation] = []

        with db_session:
            # Build work centers data for response (same as schedule-batch)
            for work_center in WorkCenter.select():
                machines_in_wc = []
                for machine in work_center.machines:
                    machines_in_wc.append({
                        "id": str(machine.id),
                        "name": machine.make,
                        "model": machine.model,
                        "type": machine.type
                    })

                work_centers_data.append(
                    WorkCenterMachine(
                        work_center_code=work_center.code,
                        work_center_name=work_center.work_center_name or "",
                        machines=machines_in_wc,
                        is_schedulable=work_center.is_schedulable
                    )
                )

            # Pick current schedule history: active first, else highest version
            current_history = select(h for h in ScheduleHistory if h.is_active == True).order_by(
                lambda h: desc(h.generated_at)).first()
            if not current_history:
                current_history = select(h for h in ScheduleHistory).order_by(lambda h: desc(h.version)).first()

            if not current_history:
                return ScheduleResponse(
                    scheduled_operations=[],
                    overall_end_time=datetime.utcnow(),
                    overall_time="0",
                    daily_production={},
                    component_status={},
                    partially_completed=["No schedule history found"],
                    work_centers=work_centers_data
                )

            # Consider only active production orders (to mirror schedule-batch semantics)
            active_production_orders = set(
                select(p.production_order for p in PartScheduleStatus if p.status == 'active')[:])

            # Preload machine display names
            machine_details = {}
            for machine in Machine.select():
                machine_name = f"{machine.work_center.code}-{machine.make}"
                machine_details[machine.id] = {
                    'name': machine_name,
                    'id': machine.id
                }

            # Planned items only for the selected schedule history and active POs
            planned_items = select(p for p in PlannedScheduleItem
                                   if p.schedule_history == current_history and
                                   p.order.production_order in active_production_orders)[:]

            latest_versions: List[ScheduleVersion] = []
            for item in planned_items:
                version = select(v for v in ScheduleVersion if v.schedule_item == item).order_by(
                    lambda v: desc(v.version_number)).first()
                if version is not None:
                    latest_versions.append(version)

            if not latest_versions:
                return ScheduleResponse(
                    scheduled_operations=[],
                    overall_end_time=datetime.utcnow(),
                    overall_time="0",
                    daily_production={},
                    component_status={},
                    partially_completed=["No schedule versions found for current history"],
                    work_centers=work_centers_data
                )

            # Build response scheduled operations
            for version in latest_versions:
                schedule_item = version.schedule_item
                order = schedule_item.order
                operation = schedule_item.operation
                machine = schedule_item.machine

                # If planned_quantity == 1, interpret as setup and display actual duration over planned setup time
                if version.planned_quantity == 1 and hasattr(operation, 'setup_time') and operation.setup_time is not None:
                    try:
                        setup_minutes_total = int(float(operation.setup_time) * 60.0)
                    except Exception:
                        setup_minutes_total = 0
                    # Actual setup duration in minutes based on planned times
                    actual_minutes = max(0, int((version.planned_end_time - version.planned_start_time).total_seconds() // 60))
                    quantity_str = f"Setup({actual_minutes}/{setup_minutes_total}min)"
                else:
                    quantity_str = f"Process({version.completed_quantity}/{version.planned_quantity}pcs)"
                machine_name = machine_details.get(machine.id, {'name': f'Machine-{machine.id}'})['name']

                scheduled_operations.append(
                    ScheduledOperation(
                        component=order.part_number,
                        part_description=order.part_description,
                        description=operation.operation_description,
                        machine=machine_name,
                        start_time=version.planned_start_time,
                        end_time=version.planned_end_time,
                        quantity=quantity_str,
                        production_order=order.production_order
                    )
                )

            # Compute window
            min_start = min(v.planned_start_time for v in latest_versions)
            max_end = max(v.planned_end_time for v in latest_versions)
            overall_end_time = max_end or datetime.utcnow()
            total_seconds = max(0, int((max_end - min_start).total_seconds())) if latest_versions else 0

            return ScheduleResponse(
                scheduled_operations=scheduled_operations,
                overall_end_time=overall_end_time,
                overall_time=str(total_seconds),
                daily_production={},
                component_status={},
                partially_completed=[],
                work_centers=work_centers_data
            )

    except Exception as e:
        print(f"Error in latest schedule endpoint: {str(e)}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


class ScheduleUpdateResult(BaseModel):
    version: int
    inserted_count: int
    deleted_history_ids: List[int]


@router.post("/schedule/update", response_model=ScheduleUpdateResult)
async def update_schedule():
    """Archive current schedule into ScheduleHistory and generate a new one as next version.
    Steps:
    - Determine current history (active else highest version) and mark inactive
    - Create new history with version+1 and set active
    - Run scheduling algorithm to generate schedule_df
    - Persist items linked to new history with global version = history.version
    - Return the newly generated latest schedule for that history
    """
    try:
        # Determine new history version and create it
        with db_session:
            current_history = select(h for h in ScheduleHistory if h.is_active == True).order_by(
                lambda h: desc(h.generated_at)).first()
            if not current_history:
                current_history = select(h for h in ScheduleHistory).order_by(lambda h: desc(h.version)).first()

            next_version = (current_history.version if current_history else 0) + 1

            if current_history and current_history.is_active:
                current_history.is_active = False

            new_history = ScheduleHistory(version=next_version, is_active=True, generated_at=datetime.utcnow())

        # Build schedule inputs similar to schedule-batch
        with db_session:
            # Active POs
            active_production_orders = set(
                select(p.production_order for p in PartScheduleStatus if p.status == 'active')[:])

        # Fetch operations dataframe
        df = fetch_operations()
        if df.empty:
            return ScheduleUpdateResult(
                version=next_version,
                inserted_count=0,
                deleted_history_ids=[]
            )

        # Build mapping of active POs per part number and a part_po to quantity using same approach as schedule-batch
        po_to_part_mapping = {}
        part_po_to_quantity = {}
        with db_session:
            active_part_statuses = select(p for p in PartScheduleStatus if p.status == 'active')[:]
            for part_status in active_part_statuses:
                po = part_status.production_order
                part_number = part_status.part_number
                po_to_part_mapping[po] = part_number
                order = Order.get(production_order=po, part_number=part_number)
                quantity = order.launched_quantity if order else 0
                if quantity <= 0:
                    order_alt = Order.get(part_number=part_number)
                    quantity = order_alt.launched_quantity if order_alt else 10
                part_po_to_quantity[(part_number, po)] = quantity

        # Expand operations only for active POs and schedulable work centers
        expanded_rows = []
        with db_session:
            for (part_number, po), quantity in part_po_to_quantity.items():
                matching_ops = Operation.select(
                    lambda o: o.order.part_number == part_number and
                              o.order.production_order == po and
                              o.work_center.is_schedulable == True
                )
                for op in matching_ops:
                    expanded_rows.append({
                        'partno': part_number,
                        'operation': op.operation_description,
                        'machine_id': op.machine.id,
                        'sequence': op.operation_number,
                        'time': float(op.ideal_cycle_time),
                        'production_order': po,
                        'work_center_id': op.work_center.id
                    })

        if expanded_rows:
            df = pd.DataFrame(expanded_rows)
        else:
            df = pd.DataFrame()

        if df.empty:
            # Nothing to schedule; return empty result
            return ScheduleUpdateResult(
                version=next_version,
                inserted_count=0,
                deleted_history_ids=[]
            )

        # Filter to active POs
        df = df[df['production_order'].isin(active_production_orders)]

        # Filter operations to schedulable work centers based on machine -> wc mapping
        machine_to_wc = {}
        with db_session:
            for machine in Machine.select():
                machine_to_wc[machine.id] = machine.work_center.id
        df['work_center_id'] = df['machine_id'].map(machine_to_wc)

        # Run algorithm within an active db session
        with db_session:
            lead_times = fetch_lead_times()
            schedule_df, overall_end_time, overall_time, daily_production, component_status, partially_completed = schedule_operations(
                df, part_po_to_quantity, lead_times
            )

        # Persist schedule linked to new history and using global version equal to history.version
        with db_session:
            inserted = store_schedule(
                schedule_df,
                component_status,
                global_version=next_version,
                schedule_history_id=new_history.id if new_history else None,
            )

        # Update start_date in part_schedule_status table with first operation start_time
        if not schedule_df.empty:
            with db_session:
                # Group by production_order and find the first operation (earliest start_time) for each order
                idx = schedule_df.groupby('production_order')['start_time'].idxmin()
                first_operations = schedule_df.loc[idx].reset_index(drop=True)
                
                # Update PartScheduleStatus records with the first operation start_time
                for _, row in first_operations.iterrows():
                    production_order = row['production_order']
                    first_start_time = row['start_time']
                    
                    # Find the PartScheduleStatus record for this production order
                    part_schedule_status = PartScheduleStatus.get(production_order=production_order)
                    if part_schedule_status:
                        part_schedule_status.start_date = first_start_time
                        # print(f"Updated start_date for production order {production_order} to {first_start_time}")
                    else:
                        print(f"Warning: No PartScheduleStatus record found for production order {production_order}")

        # After successful insert, delete all previous histories and their related data
        deleted_ids: List[int] = []
        with db_session:
            old_histories = select(h for h in ScheduleHistory if h.id != new_history.id)[:]
            deleted_ids = [h.id for h in old_histories]
            if deleted_ids:
                # Delete dependent versions and items first, then histories
                select(v for v in ScheduleVersion if v.schedule_item.schedule_history.id in deleted_ids).delete(bulk=True)
                select(p for p in PlannedScheduleItem if p.schedule_history.id in deleted_ids).delete(bulk=True)
                select(h for h in ScheduleHistory if h.id in deleted_ids).delete(bulk=True)

        # Return the newly created version id, number of records inserted, and deleted history ids
        return ScheduleUpdateResult(
            version=next_version,
            inserted_count=len(inserted or []),
            deleted_history_ids=deleted_ids,
        )

    except Exception as e:
        print(f"Error in update schedule endpoint: {str(e)}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/part-schedule-start-date/{production_order}/{part_number}", response_model=PartScheduleStartDateResponse)
async def get_part_schedule_start_date(production_order: str, part_number: str):
    """Get the start_date for a specific production order and part number"""
    try:
        with db_session:
            # Find the PartScheduleStatus record for the given production order and part number
            part_schedule = PartScheduleStatus.get(
                production_order=production_order, 
                part_number=part_number
            )
            
            if not part_schedule:
                raise HTTPException(
                    status_code=404, 
                    detail=f"No schedule status found for production order {production_order} and part number {part_number}"
                )
            
            # Return the start_date (can be None if not set)
            return PartScheduleStartDateResponse(
                start_date=part_schedule.start_date
            )
            
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

