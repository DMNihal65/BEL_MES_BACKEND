import traceback
import calendar
from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

import pandas as pd
from dateutil import parser
from fastapi import APIRouter, HTTPException, Query
from pony.orm import db_session, select, ObjectNotFound
from pydantic import BaseModel
from app.crud.operation import fetch_operations
from app.crud.component_quantities import fetch_component_quantities
from app.crud.leadtime import fetch_lead_times
from app.algorithm.planned_scheduling import schedule_operations
import re

from app.schemas.operations import WorkCenterMachine
from app.schemas.scheduled1 import ScheduleResponse, ProductionLogsResponse, ProductionLogResponse, ScheduledOperation, \
    CombinedScheduleProductionResponse, PartProductionResponse, PartProductionTimeline, PartStatusUpdate, \
    MachineUtilization, OrderCompletionRequest, OrderCompletionResponse, OrderCompletionStatus, \
    AllCompletionStatusResponse, OrderCompletionRecord
from app.models.master_order import OrderCompleted
from app.models import Order, Operation, Machine, PartScheduleStatus, ScheduleVersion, \
    ProductionLog, WorkCenter
from datetime import time as dt_time
from app.models.scheduled import PlannedItem
from app.models.master_order import MachineStatus

router = APIRouter(prefix="/api/v1/scheduling-planned", tags=["scheduling-planned"])

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
                print(f"Machine with ID {machine_id} not found")
                continue

            total_qty, current_qty, _ = extract_quantity(row['quantity'])

            # Get activation time from PartScheduleStatus (IST) for reference
            part_status = PartScheduleStatus.get(production_order=production_order)
            if not part_status:
                print(f"No PartScheduleStatus found for production order {production_order}")
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
                # print(f"Invalidating old schedule with wrong start_time for order {order.id}, operation {operation.id}, machine {machine.id}")
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
                # print(f"Exact duplicate schedule found for order {order.id}, operation {operation.id}, machine {machine.id}")
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
    
    
@router.get("/schedule/planned", response_model=ScheduleResponse)
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



