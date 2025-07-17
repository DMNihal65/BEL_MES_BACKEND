import traceback
import calendar
from collections import defaultdict
from datetime import datetime
from typing import List, Dict, Any, Optional

import pandas as pd
from dateutil import parser
from fastapi import APIRouter, HTTPException, Query
from pony.orm import db_session, select, ObjectNotFound
from pydantic import BaseModel

from app.models import Order, Operation, Machine, PartScheduleStatus, PlannedScheduleItem, ScheduleVersion, \
    ProductionLog, WorkCenter
from app.crud.operation import fetch_operations
from app.crud.component_quantities import fetch_component_quantities
from app.crud.leadtime import fetch_lead_times
from app.algorithm.scheduling import schedule_operations
import re

from app.schemas.operations import WorkCenterMachine
from app.schemas.scheduled1 import ScheduleResponse, ProductionLogsResponse, ProductionLogResponse, ScheduledOperation, \
    CombinedScheduleProductionResponse, PartProductionResponse, PartProductionTimeline, PartStatusUpdate, \
    MachineUtilization

router = APIRouter(prefix="/api/v1/scheduling", tags=["scheduling"])

from datetime import datetime, timezone, timedelta


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
            else:
                # Only update the timestamp if changing from inactive to active
                if status_record.status == 'inactive' and final_status == 'active':
                    status_record.updated_at = current_time_utc

                # Always update the status
                status_record.status = final_status

            # Format the activation timestamp to include both date and time in IST
            activation_time_str = current_time_ist.strftime("%Y-%m-%d %H:%M:%S") if final_status == 'active' else None

            return {
                "message": f"Production order {production_order} status set to {final_status}",
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


@db_session
def store_schedule(schedule_df, component_status):
    """Store the generated schedule in the database, avoiding duplicate entries"""
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
                print(f"No order found for part {part_no} with production order {production_order}")
                continue

            # Find the specific operation for this order and operation description
            operation = Operation.select(
                lambda op: op.order == order and op.operation_description == operation_desc
            ).first()

            if not operation:
                print(f"No operation found for order {order.id} with description {operation_desc}")
                continue

            try:
                machine = Machine[machine_id]
            except ObjectNotFound:
                print(f"Machine with ID {machine_id} not found")
                continue

            total_qty, current_qty, _ = extract_quantity(row['quantity'])

            print('&'*50)
            print(type(row['start_time']))
            print('&'*50)

            start_time = row['start_time'].to_pydatetime()
            end_time = row['end_time'].to_pydatetime()


            # start_time = row['start_time']
            # end_time = row['end_time']

            # Check if this exact schedule already exists
            existing_schedule = PlannedScheduleItem.select(
                lambda s: s.order == order and
                          s.operation == operation and
                          s.machine == machine and
                          s.initial_start_time == start_time and
                          s.initial_end_time == end_time and
                          s.total_quantity == total_qty
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
                current_version=1
            )

            schedule_version = ScheduleVersion(
                schedule_item=schedule_item,
                version_number=1,
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


# @router.get("/schedule-batch/", response_model=ScheduleResponse)
# async def schedule():
#     """Generate schedule for active parts and store in database"""
#     try:
#         # Initialize work_centers_data at the start
#         work_centers_data = []
#
#         # Always fetch work centers data regardless of active production orders
#         with db_session:
#             # Fetch work centers and their machines for the response
#             for work_center in WorkCenter.select():
#                 machines_in_wc = []
#                 for machine in work_center.machines:
#                     machines_in_wc.append({
#                         "id": str(machine.id),
#                         "name": machine.make,
#                         "model": machine.model,
#                         "type": machine.type
#                     })
#
#                 work_centers_data.append(
#                     WorkCenterMachine(
#                         work_center_code=work_center.code,
#                         work_center_name=work_center.work_center_name or "",
#                         machines=machines_in_wc,
#                         is_schedulable=work_center.is_schedulable  # Include the flag in response
#                     )
#                 )
#
#             # Log schedulable work centers
#             schedulable_work_centers = [wc for wc in WorkCenter.select() if wc.is_schedulable]
#             print(f"Schedulable work centers: {[wc.code for wc in schedulable_work_centers]}")
#
#             # Get list of schedulable work center IDs for filtering
#             schedulable_work_center_ids = {wc.id for wc in schedulable_work_centers}
#
#             # Fetch all production orders with active status
#             active_production_orders = select(p.production_order for p in PartScheduleStatus if p.status == 'active')[:]
#
#             # Convert to a set for faster lookups
#             active_production_orders_set = set(active_production_orders)
#
#             print(f"Active production orders: {active_production_orders_set}")
#
#             if not active_production_orders_set:
#                 print("No active production orders found")
#                 return ScheduleResponse(
#                     scheduled_operations=[],
#                     overall_end_time=datetime.utcnow(),
#                     overall_time="0",
#                     daily_production={},
#                     component_status={},
#                     partially_completed=["No parts are marked as active for scheduling"],
#                     work_centers=work_centers_data  # Return work centers even if no active orders
#                 )
#
#             # Get mapping of production orders to part numbers and required quantities
#             po_to_part_mapping = {}
#             part_po_to_quantity = {}
#
#             # Get all active part statuses with their required quantities
#             active_part_statuses = select(p for p in PartScheduleStatus if p.status == 'active')[:]
#
#             for part_status in active_part_statuses:
#                 po = part_status.production_order
#                 part_number = part_status.part_number
#                 po_to_part_mapping[po] = part_number
#
#                 # Get quantity from Order if possible
#                 order = Order.get(production_order=po, part_number=part_number)
#                 quantity = order.launched_quantity if order else 0
#
#                 # If no quantity found in Order, use a default value
#                 if quantity <= 0:
#                     # Try to find the associated Order and get its launched_quantity
#                     order = Order.get(part_number=part_number)
#                     quantity = order.launched_quantity if order else 10  # Default to 10 if no quantity found
#
#                 # Store the part-PO specific quantity
#                 part_po_to_quantity[(part_number, po)] = quantity
#
#         # Fetch operations for all parts
#         df = fetch_operations()
#
#         if df.empty:
#             print("No operations found in fetch_operations()")
#             return ScheduleResponse(
#                 scheduled_operations=[],
#                 overall_end_time=datetime.utcnow(),
#                 overall_time="0",
#                 daily_production={},
#                 component_status={},
#                 partially_completed=["No operations found in database"],
#                 work_centers=work_centers_data  # Return work centers even if no operations
#             )
#
#         print(f"Original operations dataframe shape: {df.shape}")
#         print(f"Columns in operations dataframe: {df.columns.tolist()}")
#
#         # Add production_order column to dataframe
#         if 'production_order' not in df.columns:
#             # Maps part numbers to their active production orders
#             part_to_pos = {}
#             for part_number in df['partno'].unique():
#                 part_to_pos[part_number] = []
#                 for po in active_production_orders_set:
#                     if po_to_part_mapping.get(po) == part_number:
#                         part_to_pos[part_number].append(po)
#
#             # Expand the dataframe to include all active production orders
#             # Filter for operations from schedulable work centers and expand the dataframe
#             expanded_rows = []
#             for (part_number, po), quantity in part_po_to_quantity.items():
#                 # Query actual operations for this specific (part_number, production_order)
#                 # CRITICAL FIX: Only include operations from work centers that are marked as schedulable
#                 matching_ops = Operation.select(
#                     lambda o: o.order.part_number == part_number and
#                               o.order.production_order == po and
#                               o.work_center.is_schedulable == True  # Explicit check for is_schedulable=True
#                 )
#
#                 for op in matching_ops:
#                     expanded_rows.append({
#                         'partno': part_number,
#                         'operation': op.operation_description,
#                         'machine_id': op.machine.id,
#                         'sequence': op.operation_number,
#                         'time': float(op.ideal_cycle_time),
#                         'production_order': po,
#                         'work_center_id': op.work_center.id  # Add work center ID for filtering
#                     })
#
#             if expanded_rows:
#                 df = pd.DataFrame(expanded_rows)
#             else:
#                 df = pd.DataFrame()  # Empty dataframe if no active production orders found
#
#         # Double check if we have any operations for active production orders
#         if df.empty:
#             print("No operations left after filtering for active production orders and schedulable work centers")
#             return ScheduleResponse(
#                 scheduled_operations=[],
#                 overall_end_time=datetime.utcnow(),
#                 overall_time="0",
#                 daily_production={},
#                 component_status={},
#                 partially_completed=["No operations found for active production orders in schedulable work centers"],
#                 work_centers=work_centers_data  # Return work centers even if no operations for active orders
#             )
#
#         # Filter to keep only rows with active production orders
#         df = df[df['production_order'].isin(active_production_orders_set)]
#
#         # ADDITIONAL FILTER: Ensure all operations are from schedulable work centers
#         # Create a mapping from machine_id to work_center_id
#         machine_to_wc = {}
#         with db_session:
#             for machine in Machine.select():
#                 machine_to_wc[machine.id] = machine.work_center.id
#
#         # Add a column with work center ID for each operation based on its machine
#         df['work_center_id'] = df['machine_id'].map(machine_to_wc)
#
#         # Filter out operations from non-schedulable work centers
#         df = df[df['work_center_id'].isin(schedulable_work_center_ids)]
#
#         if df.empty:
#             print("No operations left after filtering for schedulable work centers")
#             return ScheduleResponse(
#                 scheduled_operations=[],
#                 overall_end_time=datetime.utcnow(),
#                 overall_time="0",
#                 daily_production={},
#                 component_status={},
#                 partially_completed=["All operations are in non-schedulable work centers"],
#                 work_centers=work_centers_data
#             )
#
#         # Get the active part numbers based on the filtered dataframe
#         active_part_numbers_in_df = df['partno'].unique().tolist()
#         print(f"Active part numbers in filtered dataframe: {active_part_numbers_in_df}")
#
#         # Create component_quantities dictionary with the correct format
#         component_quantities = {}
#         for _, row in df.iterrows():
#             part_number = row['partno']
#             production_order = row['production_order']
#             key = (part_number, production_order)
#
#             # Use the saved quantity for this part-PO combination
#             if key not in component_quantities and key in part_po_to_quantity:
#                 component_quantities[key] = part_po_to_quantity[key]
#
#         print(f"Component quantities for scheduling: {component_quantities}")
#
#         # Get lead times
#         lead_times = fetch_lead_times()
#
#         # Filter lead_times to only include parts in filtered operations
#         lead_times = {k: v for k, v in lead_times.items() if k in active_part_numbers_in_df}
#
#         # Call scheduling algorithm with filtered dataframe and properly structured component_quantities
#         schedule_df, overall_end_time, overall_time, daily_production, \
#             component_status, partially_completed = schedule_operations(
#             df, component_quantities, lead_times
#         )
#
#         # Final verification
#         if not schedule_df.empty:
#             print(f"Final schedule has {len(schedule_df)} operations")
#             print(f"Production orders in final schedule: {schedule_df['production_order'].unique().tolist()}")
#
#             # Verify all production orders in the schedule are active
#             scheduled_pos = set(schedule_df['production_order'].unique())
#             invalid_pos = scheduled_pos - active_production_orders_set
#             if invalid_pos:
#                 print(f"WARNING: Found inactive production orders in schedule: {invalid_pos}")
#                 # Filter out any operations with inactive production orders
#                 schedule_df = schedule_df[schedule_df['production_order'].isin(active_production_orders_set)]
#
#         # Filter component_status to only include entries with active production orders
#         filtered_component_status = {}
#         for key, status in component_status.items():
#             production_order = None
#
#             # Check if this is a combined key (partno_production_order)
#             if '_' in key:
#                 partno, production_order = key.split('_', 1)
#             else:
#                 production_order = status.get('production_order')
#
#             # Only include if the production_order is active
#             if production_order in active_production_orders_set:
#                 filtered_component_status[key] = status
#
#         # Replace the original component_status with filtered version
#         component_status = filtered_component_status
#         print(f"Filtered component_status keys: {list(component_status.keys())}")
#
#         stored_schedule = None
#         if not schedule_df.empty:
#             with db_session:
#                 stored_schedule = store_schedule(schedule_df, component_status)
#
#         scheduled_operations = []
#         if not schedule_df.empty:
#             with db_session:
#                 machine_details = {}
#                 for machine in Machine.select():
#                     machine_name = f"{machine.work_center.code}-{machine.make}"
#                     machine_details[machine.id] = {
#                         'name': machine_name,
#                         'id': machine.id
#                     }
#
#             # Convert schedule dataframe to response objects
#             for _, row in schedule_df.iterrows():
#                 machine_id = row['machine_id']
#                 machine_name = machine_details.get(machine_id, {'name': f'Machine-{machine_id}'})['name']
#
#                 # Get production_order
#                 production_order = row.get('production_order')
#
#                 # Double-check that this is an active production order
#                 if production_order not in active_production_orders_set:
#                     print(f"Skipping operation for inactive production order: {production_order}")
#                     continue
#
#                 scheduled_operations.append(
#                     ScheduledOperation(
#                         component=row['partno'],
#                         description=row['operation'],
#                         machine=machine_name,
#                         start_time=row['start_time'],
#                         end_time=row['end_time'],
#                         quantity=row['quantity'],
#                         production_order=production_order
#                     )
#                 )
#
#         # Ensure correct types for overall_end_time
#         if overall_end_time is None:
#             overall_end_time = datetime.utcnow()
#
#         # Convert daily_production from list to dict if needed
#         if isinstance(daily_production, list):
#             daily_production = {}
#
#         # Always return work_centers_data, even if it's empty
#         return ScheduleResponse(
#             scheduled_operations=scheduled_operations,
#             overall_end_time=overall_end_time,
#             overall_time=str(overall_time),
#             daily_production=daily_production,
#             component_status=component_status,
#             partially_completed=partially_completed,
#             work_centers=work_centers_data  # Always return work centers data
#         )
#
#     except Exception as e:
#         print(f"Error in schedule endpoint: {str(e)}")
#         traceback.print_exc()  # Add this for full error details
#         raise HTTPException(status_code=500, detail=str(e))



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
            print(f"Schedulable work centers: {[wc.code for wc in schedulable_work_centers]}")

            # Get list of schedulable work center IDs for filtering
            schedulable_work_center_ids = {wc.id for wc in schedulable_work_centers}

            # Fetch all production orders with active status
            active_production_orders = select(p.production_order for p in PartScheduleStatus if p.status == 'active')[:]

            # Convert to a set for faster lookups
            active_production_orders_set = set(active_production_orders)

            print(f"Active production orders: {active_production_orders_set}")

            if not active_production_orders_set:
                print("No active production orders found")
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

        print(f"Original operations dataframe shape: {df.shape}")
        print(f"Columns in operations dataframe: {df.columns.tolist()}")

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
            print("No operations left after filtering for active production orders and schedulable work centers")
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
        print(f"Active part numbers in filtered dataframe: {active_part_numbers_in_df}")

        # Create component_quantities dictionary with the correct format
        component_quantities = {}
        for _, row in df.iterrows():
            part_number = row['partno']
            production_order = row['production_order']
            key = (part_number, production_order)

            # Use the saved quantity for this part-PO combination
            if key not in component_quantities and key in part_po_to_quantity:
                component_quantities[key] = part_po_to_quantity[key]

        print(f"Component quantities for scheduling: {component_quantities}")

        # Get lead times
        lead_times = fetch_lead_times()

        # Filter lead_times to only include parts in filtered operations
        lead_times = {k: v for k, v in lead_times.items() if k in active_part_numbers_in_df}

        # Call scheduling algorithm with filtered dataframe and properly structured component_quantities
        schedule_df, overall_end_time, overall_time, daily_production, \
            component_status, partially_completed = schedule_operations(
            df, component_quantities, lead_times
        )

        # Final verification
        if not schedule_df.empty:
            print(f"Final schedule has {len(schedule_df)} operations")
            print(f"Production orders in final schedule: {schedule_df['production_order'].unique().tolist()}")

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
        print(f"Filtered component_status keys: {list(component_status.keys())}")

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
                    print(f"Skipping operation for inactive production order: {production_order}")
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

            # Get all active ScheduleVersions with related data, filtered by active parts
            versions_query = select((
                                        version,
                                        version.schedule_item,
                                        version.schedule_item.order,
                                        version.schedule_item.operation,
                                        version.schedule_item.machine
                                    ) for version in ScheduleVersion
                                    if version.is_active == True and
                                    version.schedule_item.order.part_number in active_parts)

            # Dictionary to store all operations by part number and production order
            part_operations = defaultdict(list)

            # Group operations by part number and production order
            for (version, schedule_item, order, operation, machine) in versions_query:
                # Use a composite key of part_number and production_order
                key = (order.part_number, order.production_order)

                # Extract the proper quantity from the version
                total_qty = version.planned_quantity

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
                    'start_time': version.planned_start_time,
                    'end_time': version.planned_end_time,
                    'total_quantity': total_qty,
                    'remaining_quantity': version.remaining_quantity,
                    'completed_quantity': version.completed_quantity,
                    'version_number': version.version_number,
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

                # print('^^^'*50)
                # print(operations)
                # print('^^^'*50)

                results.append(PartProductionTimeline(
                    part_number=part_number,
                    production_order=production_order,
                    completed_total_quantity=total_quantity,
                    operations_count=len(operations),
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
    - Available hours: working hours (8) * working days in month * 0.85 (efficiency)
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

    # Calculate working days in the month (excluding weekends)
    _, days_in_month = calendar.monthrange(year, month)
    working_days = 0
    for day in range(1, days_in_month + 1):
        weekday = datetime(year, month, day).weekday()
        # 0-4 are Monday to Friday (working days)
        if weekday < 5:
            working_days += 1

    # Calculate available hours
    # Formula: working hours (8) * working days in month * 0.85 (efficiency)
    efficiency_factor = 0.85
    daily_working_hours = 8

    # Monthly calculation based on working days only
    available_hours = working_days * daily_working_hours * efficiency_factor

    # Set date range for the month
    start_date = datetime(year, month, 1)
    if month == 12:
        end_date = datetime(year + 1, 1, 1)
    else:
        end_date = datetime(year, month + 1, 1)

    # Get all active production orders first
    active_production_orders = select(ps.production_order for ps in PartScheduleStatus if ps.status == 'active')[:]

    # Query to fetch machines
    machines_query = select(m for m in Machine)
    if machine_id:
        machines_query = machines_query.filter(lambda m: m.id == machine_id)

    machines = machines_query[:]

    result = []
    for machine in machines:
        # Get planned schedule items for this machine in the given month
        # Only include items for active production orders
        schedule_items = select(p for p in PlannedScheduleItem
                                if p.machine.id == machine.id
                                and p.order.production_order in active_production_orders
                                and ((p.initial_start_time >= start_date and p.initial_start_time < end_date) or
                                     (p.initial_end_time > start_date and p.initial_end_time <= end_date) or
                                     (p.initial_start_time <= start_date and p.initial_end_time >= end_date)))

        # Calculate utilized hours from planned schedule items
        utilized_hours = 0
        # Track hours used per day to prevent counting more than daily_working_hours per day
        daily_hours = {}

        for item in schedule_items:
            # Handle cases where schedule item spans across months
            actual_start = max(item.initial_start_time, start_date)
            actual_end = min(item.initial_end_time, end_date)

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

        # Ensure utilized hours don't exceed available hours
        utilized_hours = min(utilized_hours, available_hours)

        # Calculate remaining and utilization percentage
        remaining_hours = max(0, available_hours - utilized_hours)
        utilization_percentage = (utilized_hours / available_hours * 100) if available_hours > 0 else 0

        # Get the work center name from the related work center
        work_center_name = machine.work_center.work_center_name if machine.work_center else None

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
    if start_date >= end_date:
        raise HTTPException(status_code=400, detail="End date must be after start date")

    # Calculate working days in the range (excluding weekends)
    working_days = 0
    current_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = end_date.replace(hour=0, minute=0, second=0, microsecond=0)

    while current_date < end_day:
        weekday = current_date.weekday()
        # 0-4 are Monday to Friday (working days)
        if weekday < 5:
            working_days += 1
        current_date += timedelta(days=1)

    # Calculate available hours
    # Formula: working hours (8) * working days in range * 0.85 (efficiency)
    efficiency_factor = 0.85
    daily_working_hours = 15

    # Available hours for the date range based on working days only
    available_hours = working_days * daily_working_hours * efficiency_factor

    # Get all active production orders first
    active_production_orders = select(ps.production_order for ps in PartScheduleStatus if ps.status == 'active')[:]

    # Query to fetch machines
    machines_query = select(m for m in Machine)
    if machine_id:
        machines_query = machines_query.filter(lambda m: m.id == machine_id)

    machines = machines_query[:]

    result = []
    for machine in machines:
        # Get planned schedule items for this machine in the given date range
        # Only include items for active production orders
        schedule_items = select(p for p in PlannedScheduleItem
                                if p.machine.id == machine.id
                                and p.order.production_order in active_production_orders
                                and ((p.initial_start_time >= start_date and p.initial_start_time < end_date) or
                                     (p.initial_end_time > start_date and p.initial_end_time <= end_date) or
                                     (p.initial_start_time <= start_date and p.initial_end_time >= end_date)))

        # Calculate utilized hours from planned schedule items
        utilized_hours = 0
        # Track hours used per day to prevent counting more than daily_working_hours per day
        daily_hours = {}

        for item in schedule_items:
            # Handle cases where schedule item spans across the date range boundaries
            actual_start = max(item.initial_start_time, start_date)
            actual_end = min(item.initial_end_time, end_date)

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

        # Ensure utilized hours don't exceed available hours
        utilized_hours = min(utilized_hours, available_hours)

        # Calculate remaining and utilization percentage
        remaining_hours = max(0, available_hours - utilized_hours)
        utilization_percentage = (utilized_hours / available_hours * 100) if available_hours > 0 else 0

        # Get the work center name from the related work center
        work_center_name = machine.work_center.code if machine.work_center else None

        result.append(MachineUtilization(
            machine_id=machine.id,
            machine_type=machine.type,
            machine_make=machine.make,
            machine_model=machine.model,
            work_center_name=work_center_name,
            work_center_bool= machine.work_center.is_schedulable,
            available_hours=round(available_hours, 2),
            utilized_hours=round(utilized_hours, 2),
            remaining_hours=round(remaining_hours, 2),
            utilization_percentage=round(utilization_percentage, 2)
        ))

    return result