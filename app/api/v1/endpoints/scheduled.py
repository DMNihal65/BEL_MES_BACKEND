import traceback
import calendar
from collections import defaultdict
from datetime import datetime
from typing import List, Dict, Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pony.orm import db_session, select, ObjectNotFound

from app.models import Order, Operation, Machine, PartScheduleStatus, PlannedScheduleItem, ScheduleVersion, \
    ProductionLog, WorkCenter
from app.crud.operation import fetch_operations
from app.crud.component_quantities import fetch_component_quantities
from app.crud.leadtime import fetch_lead_times
from app.algorithm.scheduling import schedule_operations
import re

from app.schemas.operations import WorkCenterMachine
from app.schemas.scheduled1 import ScheduleResponse, ProductionLogsResponse, ProductionLogResponse, ScheduledOperation, \
    CombinedScheduleProductionResponse, PartProductionResponse, PartProductionTimeline, PartStatusUpdate

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
    """Store the generated schedule in the database, handling multiple orders with the same part number"""
    try:
        stored_items = []

        for _, row in schedule_df.iterrows():
            # Fetch all matching orders with the same part number
            matching_orders = list(Order.select(lambda o: o.part_number == row['partno']))

            if not matching_orders:
                continue

            for order in matching_orders:
                matching_operations = list(Operation.select(
                    lambda op: op.order == order and op.operation_description == row['operation']
                ))

                if not matching_operations:
                    continue

                operation = matching_operations[0]
                machine_id = row['machine_id']

                try:
                    machine = Machine[machine_id]
                except ObjectNotFound:
                    print(f"Machine with ID {machine_id} not found")
                    continue

                total_qty, current_qty, _ = extract_quantity(row['quantity'])
                start_time = row['start_time'].to_pydatetime()
                end_time = row['end_time'].to_pydatetime()

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
                        machines=machines_in_wc
                    )
                )

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

            # Get mapping of production orders to part numbers
            po_to_part_mapping = {po: pn for po, pn in
                                  select((p.production_order, p.part_number) for p in PartScheduleStatus)[:]
                                  }

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

        # IMPORTANT: Filter operations to ONLY include those with active production orders
        if 'production_order' in df.columns:
            # Keep only operations with active production orders
            original_row_count = len(df)
            df = df[df['production_order'].isin(active_production_orders_set)]
            print(f"Filtered from {original_row_count} to {len(df)} rows based on active production orders")
        else:
            # We need to manually map operations to active production orders
            print("No production_order column in operations dataframe, adding it")

            # Maps part numbers to their active production orders
            active_part_to_po = {}
            for po in active_production_orders_set:
                part_number = po_to_part_mapping.get(po)
                if part_number:
                    active_part_to_po[part_number] = po

            print(f"Active part to PO mapping: {active_part_to_po}")

            # Function to assign active production orders based on part number
            def get_active_production_order(row):
                part_no = row['partno']
                return active_part_to_po.get(part_no)

            # Add production_order column
            df['production_order'] = df.apply(get_active_production_order, axis=1)

            # Drop rows that don't have an active production order assigned
            original_row_count = len(df)
            df = df.dropna(subset=['production_order'])
            print(f"Filtered from {original_row_count} to {len(df)} rows after adding production orders")

        # Double check if we have any operations for active production orders
        if df.empty:
            print("No operations left after filtering for active production orders")
            return ScheduleResponse(
                scheduled_operations=[],
                overall_end_time=datetime.utcnow(),
                overall_time="0",
                daily_production={},
                component_status={},
                partially_completed=["No operations found for active production orders"],
                work_centers=work_centers_data  # Return work centers even if no operations for active orders
            )

        # Get the active part numbers based on the filtered dataframe for components and lead times
        active_part_numbers_in_df = df['partno'].unique().tolist()
        print(f"Active part numbers in filtered dataframe: {active_part_numbers_in_df}")

        component_quantities = fetch_component_quantities()
        lead_times = fetch_lead_times()

        # Filter component_quantities and lead_times to only include parts in filtered operations
        component_quantities = {k: v for k, v in component_quantities.items() if k in active_part_numbers_in_df}
        lead_times = {k: v for k, v in lead_times.items() if k in active_part_numbers_in_df}

        # Call scheduling algorithm with filtered dataframe
        schedule_df, overall_end_time, overall_time, daily_production, \
            component_status, partially_completed = schedule_operations(
            df, component_quantities, lead_times
        )

        # Keep tracking production_order in the result
        if not schedule_df.empty and 'production_order' not in schedule_df.columns:
            print("Warning: scheduling result doesn't have production_order column")

            # Create mapping from partno to production order from filtered df
            partno_to_po = {}
            for _, row in df.iterrows():
                partno_to_po[row['partno']] = row['production_order']

            # Add production_order to schedule_df
            schedule_df['production_order'] = schedule_df['partno'].map(partno_to_po)

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

                scheduled_operations.append(
                    ScheduledOperation(
                        component=row['partno'],
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
    """Retrieve the production timeline for each part number using schedule_versions table"""
    try:
        with db_session:
            # Get all active ScheduleVersions with related data
            versions_query = select((
                                        version,
                                        version.schedule_item,
                                        version.schedule_item.order,
                                        version.schedule_item.operation,
                                        version.schedule_item.machine
                                    ) for version in ScheduleVersion if version.is_active == True)

            # Dictionary to store all operations by part number
            part_operations = defaultdict(list)

            # Group operations by part number
            for (version, schedule_item, order, operation, machine) in versions_query:
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

                part_operations[order.part_number].append({
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
            for part_number, operations in part_operations.items():
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
                    order = Order.get(part_number=part_number)
                    order_quantity = order.quantity if order and hasattr(order, 'quantity') else max_quantity

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

                results.append(PartProductionTimeline(
                    part_number=part_number,
                    production_order=operations[0]['production_order'],
                    completed_total_quantity=total_quantity,
                    operations_count=len(operations),
                    status=status
                ))

            # Sort by part number alphabetically
            results.sort(key=lambda x: x.part_number)

            return PartProductionResponse(
                items=results,
                total_parts=len(results)
            )

    except Exception as e:
        print(f"Error retrieving part production timeline: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/machine-utilization", response_model=List[Dict[str, Any]])
async def get_machine_utilization(
        year: int = Query(datetime.now().year, description="Year to calculate utilization for"),
        month: int = Query(None, description="Month to calculate utilization for (optional)")
):
    """
    Calculate machine utilization for each machine.

    Utilization formula:
    - Available time = working hours * days in month * months * efficiency factor (0.85)
    - Utilized time = Sum of production times from logs
    - Utilization rate = (Utilized time / Available time) * 100%
    """
    try:
        with db_session:
            # Get all machines
            machines = select(m for m in Machine)[:]

            if not machines:
                return []

            results = []

            for machine in machines:
                # Calculate available time - consider 24 hours for full day operation
                working_hours = 24  # Changed from 8 to 24 hours for full day operation

                if month:
                    # Calculate for specific month - use all days in the month
                    days_in_month = calendar.monthrange(year, month)[1]
                    start_date = datetime(year, month, 1)
                    if month == 12:
                        end_date = datetime(year + 1, 1, 1)
                    else:
                        end_date = datetime(year, month + 1, 1)
                else:
                    # Calculate for entire year - use all days in each month
                    days_in_month = 365 if not calendar.isleap(year) else 366
                    start_date = datetime(year, 1, 1)
                    end_date = datetime(year + 1, 1, 1)

                efficiency_factor = 0.85

                # Available time in hours
                available_time = working_hours * days_in_month * efficiency_factor

                # Get all production logs for this machine in the specified date range
                production_logs = select(
                    log for log in ProductionLog
                    for sv in log.schedule_version
                    for si in sv.schedule_item
                    if si.machine.id == machine.id and
                    log.start_time >= start_date and
                    log.start_time < end_date
                )[:]

                # Track which schedule versions have logs
                processed_version_ids = set(log.schedule_version.id for log in production_logs
                                            if hasattr(log, 'schedule_version') and log.schedule_version)

                # Calculate utilized time from production logs
                utilized_time = 0

                for log in production_logs:
                    if log.end_time:
                        # If end_time is available, use actual duration
                        duration = (log.end_time - log.start_time).total_seconds() / 3600  # Convert to hours
                        utilized_time += duration
                    else:
                        # If end_time is not available, estimate based on schedule
                        schedule_version = log.schedule_version
                        if schedule_version:
                            scheduled_duration = (schedule_version.planned_end_time -
                                                  schedule_version.planned_start_time).total_seconds() / 3600
                            # Adjust duration based on completion percentage if possible
                            if schedule_version.planned_quantity > 0 and log.quantity_completed is not None:
                                completion_ratio = min(1.0, log.quantity_completed / schedule_version.planned_quantity)
                                utilized_time += scheduled_duration * completion_ratio

                # Only add scheduled items without logs
                scheduled_items = select(
                    si for si in PlannedScheduleItem
                    if si.machine.id == machine.id and
                    si.initial_start_time >= start_date and
                    si.initial_start_time < end_date
                )[:]

                for item in scheduled_items:
                    active_versions = select(
                        sv for sv in item.schedule_versions
                        if sv.is_active and sv.id not in processed_version_ids
                    )[:]

                    for version in active_versions:
                        # Make sure the schedule falls within our date range
                        if (version.planned_start_time < end_date and
                                (version.planned_end_time is None or version.planned_end_time >= start_date)):
                            # Calculate actual duration within our date range
                            effective_start = max(version.planned_start_time, start_date)
                            effective_end = min(version.planned_end_time,
                                                end_date) if version.planned_end_time else end_date

                            duration = (effective_end - effective_start).total_seconds() / 3600
                            utilized_time += duration

                # Ensure utilized time cannot exceed available time
                utilized_time = min(utilized_time, available_time)

                # Calculate utilization rate
                utilization_rate = (utilized_time / available_time * 100) if available_time > 0 else 0

                results.append({
                    "machine_id": machine.id,
                    "machine_name": machine.make if hasattr(machine, 'make') else "Unknown",
                    "year": year,
                    "month": month if month else "All",
                    "days_used": days_in_month,
                    "available_hours": round(available_time, 2),
                    "utilized_hours": round(utilized_time, 2),
                    "utilization_rate": round(utilization_rate, 2),
                    "efficiency_factor": efficiency_factor
                })

            return results

    except Exception as e:
        import traceback
        error_details = str(e) + "\n" + traceback.format_exc()
        raise HTTPException(status_code=500, detail=f"Error calculating machine utilization: {error_details}")

