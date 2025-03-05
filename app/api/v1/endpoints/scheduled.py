from collections import defaultdict

from fastapi import APIRouter, HTTPException
from pony.orm import db_session, select
from app.schemas.scheduled import ScheduledOperation, ScheduleResponse, ProductionLogResponse, ProductionLogsResponse, \
    CombinedScheduleProductionResponse, PartProductionResponse, \
    PartProductionTimeline
from app.models import Order, Operation, Machine, PartScheduleStatus, PlannedScheduleItem, ScheduleVersion, \
    ProductionLog
from app.crud.operation import fetch_operations
from app.crud.component_quantities import fetch_component_quantities
from app.crud.leadtime import fetch_lead_times
from app.algorithm.scheduling import schedule_operations
import re

router = APIRouter(prefix="/api/v1/scheduling", tags=["scheduling"])

@router.post("/set-part-status/{part_number}")
async def set_part_status(part_number: str, status: str):
    """Set whether a part number should be included in scheduling"""
    if status not in ['active', 'inactive']:
        raise HTTPException(
            status_code=400,
            detail="Status must be 'active' or 'inactive'"
        )

    try:
        with db_session:
            # First verify part number exists in master_order
            order = Order.get(part_number=part_number)
            if not order:
                raise HTTPException(
                    status_code=404,
                    detail=f"Part number {part_number} not found in master_order"
                )

            # Find or create status record
            status_record = PartScheduleStatus.get(part_number=part_number)

            if not status_record:
                # Create new status record
                status_record = PartScheduleStatus(
                    part_number=part_number,
                    status=status
                )
            else:
                # Update existing record
                status_record.status = status

            return {
                "message": f"Part number {part_number} status set to {status}",
                "will_be_scheduled": status == 'active'
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/active-parts")
async def get_active_parts():
    """Get all parts that are currently marked as active for scheduling"""
    try:
        with db_session:
            active_items = select((
                                      p.part_number,
                                      p.status,
                                      o.production_order
                                  ) for p in PartScheduleStatus
                                  for o in Order if o.part_number == p.part_number)[:]

            return {
                "active_parts": [
                    {
                        "part_number": part_number,
                        "status": status,
                        "production_order": prod_order
                    }
                    for part_number, status, prod_order in active_items
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
    """Store the generated schedule in the database"""
    try:
        stored_items = []

        for _, row in schedule_df.iterrows():
            order = Order.get(part_number=row['partno'])
            if not order:
                continue

            matching_operations = Operation.select(
                lambda op: op.order == order and
                           op.operation_description == row['operation']
            )[:]

            if not matching_operations:
                continue

            operation = matching_operations[0]
            machine = Machine[row['machine_id']]
            if not machine:
                continue

            # Extract quantities and only use total and current for storage
            total_qty, current_qty, _ = extract_quantity(row['quantity'])

            start_time = row['start_time'].to_pydatetime()
            end_time = row['end_time'].to_pydatetime()

            # Check for existing schedule
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

            # Create new schedule item
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

            # Create new version
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
        raise e


@router.get("/schedule-batch/", response_model=ScheduleResponse)
async def schedule():
    """Generate schedule for active parts and store in database"""
    try:
        with db_session:
            ops_count = Operation.select().count()
            orders_count = Order.select().count()
            print(f"Database counts - Operations: {ops_count}, Orders: {orders_count}")

        df = fetch_operations()
        component_quantities = fetch_component_quantities()
        lead_times = fetch_lead_times()

        schedule_df, overall_end_time, overall_time, daily_production, \
            component_status, partially_completed = schedule_operations(
            df, component_quantities, lead_times
        )

        stored_schedule = None
        if not schedule_df.empty:
            with db_session:
                stored_schedule = store_schedule(schedule_df, component_status)

        scheduled_operations = []
        if not schedule_df.empty:
            with db_session:
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

                # Format the quantity string to include today's quantity
                quantity_str = f"Process({current_qty}/{total_qty}pcs, Today: {today_qty}pcs)"

                scheduled_operations.append(
                    ScheduledOperation(
                        component=row['partno'],
                        description=row['operation'],
                        machine=machine_details.get(row['machine_id'], f"Machine-{row['machine_id']}"),
                        start_time=row['start_time'],
                        end_time=row['end_time'],
                        quantity=quantity_str,
                        total_quantity=total_qty,
                        current_quantity=current_qty,
                        today_quantity=today_qty,
                        production_order=orders_map.get(row['partno'], '')
                    )
                )

        return ScheduleResponse(
            scheduled_operations=scheduled_operations,
            overall_end_time=overall_end_time,
            overall_time=str(overall_time),
            daily_production=daily_production,
            component_status=component_status,
            partially_completed=partially_completed
        )

    except Exception as e:
        print(f"Error in schedule endpoint: {str(e)}")
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