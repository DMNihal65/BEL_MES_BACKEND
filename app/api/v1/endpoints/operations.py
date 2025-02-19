from fastapi import APIRouter, HTTPException
from typing import List, Optional
from datetime import datetime, timedelta
from pony.orm import db_session, select
from app.schemas.operations import (
    OperationOut, ScheduledOperation, ScheduleResponse,
    MachineSchedulesOut, WorkCenterMachine
)
from app.crud.operation import fetch_operations
from app.crud.component_quantities import fetch_component_quantities
from app.crud.leadtime import fetch_lead_times
from app.algorithm.scheduling import schedule_operations
from app.models import Operation, Order, Machine, WorkCenter, MachineStatus, Status

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])

@router.get("/schedule-batch/", response_model=ScheduleResponse)
async def schedule():
    try:
        # Initialize work_centers_data at the start
        work_centers_data = []

        with db_session:
            ops_count = Operation.select().count()
            orders_count = Order.select().count()
            print(f"Database counts - Operations: {ops_count}, Orders: {orders_count}")

            # Fetch work centers and their machines
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

            # Fetch machine information for scheduling
            machine_info = {}
            for machine in Machine.select():
                machine_info[machine.id] = {
                    'name': f"{machine.make}",
                    'work_center': machine.work_center.code
                }

        df = fetch_operations()
        component_quantities = fetch_component_quantities()
        lead_times = fetch_lead_times()

        schedule_df, overall_end_time, overall_time, daily_production, component_status, partially_completed = \
            schedule_operations(df, component_quantities, lead_times)

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

                orders_map = {order.part_number: order.production_order for order in Order.select()}

            for _, row in schedule_df.iterrows():
                machine_id = row['machine_id']
                machine_name = machine_details.get(machine_id, {'name': f'Machine-{machine_id}'})['name']

                scheduled_operations.append(
                    ScheduledOperation(
                        component=row['partno'],
                        description=row['operation'],
                        machine=machine_name,
                        start_time=row['start_time'],
                        end_time=row['end_time'],
                        quantity=row['quantity'],
                        production_order=orders_map.get(row['partno'], '')
                    )
                )

        # Always return work_centers_data, even if it's empty
        return ScheduleResponse(
            scheduled_operations=scheduled_operations,
            overall_end_time=overall_end_time,
            overall_time=str(overall_time),
            daily_production=daily_production,
            component_status=component_status,
            partially_completed=partially_completed,
            work_centers=work_centers_data  # This will now always be included
        )

    except Exception as e:
        print(f"Error in schedule endpoint: {str(e)}")
        # In case of error, return with empty work_centers list
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/machine_schedules/", response_model=MachineSchedulesOut)
async def get_machine_schedules(
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
):
    """
    Get schedules grouped by machine
    """
    with db_session:
        df = fetch_operations()
        component_quantities = fetch_component_quantities()
        lead_times = fetch_lead_times()

        schedule_df, _, _, _, _, _ = schedule_operations(
            df, component_quantities, lead_times
        )

        machine_schedules = {}
        if not schedule_df.empty:
            # Get machine names mapping with work center info
            machine_details = {}
            for machine in Machine.select():
                machine_name = f"{machine.work_center.code}-{machine.make}"
                machine_details[machine.id] = machine_name

            for _, row in schedule_df.iterrows():
                machine_id = row['machine_id']
                machine_name = machine_details.get(machine_id, f"Machine-{machine_id}")

                if machine_name not in machine_schedules:
                    machine_schedules[machine_name] = []

                machine_schedules[machine_name].append({
                    "part_number": row['partno'],
                    "operation": row['operation'],
                    "start_time": row['start_time'],
                    "end_time": row['end_time'],
                    "duration_minutes": (row['end_time'] - row['start_time']).total_seconds() / 60
                })

        return MachineSchedulesOut(machine_schedules=machine_schedules)



@router.get("/unit_schedule/", response_model=List[ScheduledOperation])
async def unit_schedule():
    """
    Get schedule broken down to individual units
    """
    try:
        with db_session:
            df = fetch_operations()
            component_quantities = fetch_component_quantities()
            lead_times = fetch_lead_times()

            # Get production order mapping
            orders_map = {order.part_number: order.production_order for order in Order.select()}

            schedule_df, _, _, _, _, _ = schedule_operations(df, component_quantities, lead_times)

            if schedule_df.empty:
                return []

            # Get machine names mapping with work center info
            machine_details = {}
            for machine in Machine.select():
                machine_name = f"{machine.work_center.code}-{machine.make}"
                machine_details[machine.id] = machine_name

            unit_schedule_details = []
            operation_order = {}
            current_order = 0

            for _, row in schedule_df.iterrows():
                if row['operation'] not in operation_order:
                    operation_order[row['operation']] = current_order
                    current_order += 1

                try:
                    quantity_info = str(row['quantity'])
                    machine_name = machine_details.get(row['machine_id'], f"Machine-{row['machine_id']}")
                    production_order = orders_map.get(row['partno'], '')

                    # Handle setup operations
                    if quantity_info.startswith('Setup'):
                        unit_schedule_details.append(ScheduledOperation(
                            component=row['partno'],
                            description=row['operation'],
                            machine=machine_name,
                            start_time=row['start_time'],
                            end_time=row['end_time'],
                            quantity="setuptime",
                            production_order=production_order
                        ))
                    elif quantity_info.startswith('Process'):
                        process_info = quantity_info.strip('Process()').split('/')
                        if len(process_info) == 2:
                            completed_pieces = int(process_info[0].strip('pcs'))
                            total_pieces = int(process_info[1].strip('pcs'))

                            # Get operation details
                            operation = select(o for o in Operation
                                            if o.order.part_number == row['partno']
                                            and o.operation_description == row['operation']).first()

                            if operation:
                                # Calculate pieces in this block
                                previous_pieces = sum(1 for op in unit_schedule_details
                                                    if op.component == row['partno']
                                                    and op.description == row['operation']
                                                    and op.quantity != "setuptime")

                                pieces_in_block = completed_pieces - previous_pieces

                                if pieces_in_block > 0:
                                    # Calculate time per piece
                                    total_time = (row['end_time'] - row['start_time']).total_seconds()
                                    time_per_piece = total_time / pieces_in_block

                                    # Create individual piece operations
                                    for piece_idx in range(pieces_in_block):
                                        piece_number = previous_pieces + piece_idx + 1
                                        piece_start = row['start_time'] + timedelta(seconds=piece_idx * time_per_piece)
                                        piece_end = piece_start + timedelta(seconds=time_per_piece)

                                        unit_schedule_details.append(ScheduledOperation(
                                            component=row['partno'],
                                            description=row['operation'],
                                            machine=machine_name,
                                            start_time=piece_start,
                                            end_time=piece_end,
                                            quantity=f"{piece_number}/{total_pieces}",
                                            production_order=production_order
                                        ))

                except ValueError as ve:
                    print(f"Warning: Error processing operation: {str(ve)}")
                    continue
                except Exception as e:
                    print(f"Error processing operation: {str(e)}")
                    continue

            # Custom sorting function
            def sort_key(x):
                operation_seq = operation_order.get(x.description, float('inf'))
                is_setup = x.quantity == "setuptime"
                unit_number = 0
                if not is_setup:
                    try:
                        unit_number = int(x.quantity.split('/')[0])
                    except:
                        pass
                return (operation_seq, not is_setup, unit_number, x.start_time)

            # Sort and return the schedule outside the loop
            sorted_schedule = sorted(unit_schedule_details, key=sort_key)
            return sorted_schedule

    except Exception as e:
        print(f"Error in unit_schedule endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# @router.get("/machine_schedules/", response_model=MachineSchedulesOut)
# async def get_machine_schedules(
#         start_date: Optional[datetime] = None,
#         end_date: Optional[datetime] = None
# ):
#     """
#     Get schedules grouped by machine
#     """
#     with db_session:
#         df = fetch_operations()
#         component_quantities = fetch_component_quantities()
#         lead_times = fetch_lead_times()
#
#         schedule_df, _, _, _, _, _ = schedule_operations(
#             df, component_quantities, lead_times
#         )
#
#         machine_schedules = {}
#         if not schedule_df.empty:
#             # Get machine names mapping with work center info
#             machine_details = {}
#             for machine in Machine.select():
#                 machine_name = f"{machine.work_center.code}-{machine.make}"
#                 machine_details[machine.id] = machine_name
#
#             for _, row in schedule_df.iterrows():
#                 machine_id = row['machine_id']
#                 machine_name = machine_details.get(machine_id, f"Machine-{machine_id}")
#
#                 if machine_name not in machine_schedules:
#                     machine_schedules[machine_name] = []
#
#                 machine_schedules[machine_name].append({
#                     "part_number": row['partno'],
#                     "operation": row['operation'],
#                     "start_time": row['start_time'],
#                     "end_time": row['end_time'],
#                     "duration_minutes": (row['end_time'] - row['start_time']).total_seconds() / 60
#                 })
#
#         return MachineSchedulesOut(machine_schedules=machine_schedules)


