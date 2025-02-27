from fastapi import APIRouter, HTTPException
from pony.orm import db_session, select
from datetime import datetime, timedelta
from typing import Dict, Optional
from pydantic import BaseModel
from app.algorithm.scheduling import schedule_operations
from app.api.v1.endpoints.scheduled import store_schedule
from app.crud.component_quantities import fetch_component_quantities
from app.crud.leadtime import fetch_lead_times
from app.crud.operation import fetch_operations
from app.models import Order, Operation, PlannedScheduleItem


class PriorityUpdate(BaseModel):
    part_number: str
    new_priority: int

router = APIRouter(prefix="/api/v1/priority", tags=["priority"])


@router.post("/update-priority", response_model=Dict)
async def update_priority(priority_data: PriorityUpdate):
    """Update priority of a part and handle rescheduling"""
    try:
        current_time = datetime.now()
        operation_start_time = None
        blocking_operation = None

        with db_session:
            # Verify part exists and get its order
            order = Order.get(part_number=priority_data.part_number)
            if not order:
                raise HTTPException(
                    status_code=404,
                    detail=f"Part number {priority_data.part_number} not found"
                )

            # Get project and update priority
            project = order.project
            if not project:
                raise HTTPException(
                    status_code=404,
                    detail="No project found for this part"
                )

            old_priority = project.priority
            project.priority = priority_data.new_priority

            # Find current running operations on any machines used by this part
            machines_used = select(op.machine for op in Operation if op.order == order)[:]

            current_operations = select((
                                            psi,
                                            psi.order.part_number,
                                            psi.operation,
                                            psi.machine,
                                            sv
                                        ) for psi in PlannedScheduleItem
                                        for sv in psi.schedule_versions
                                        if sv.is_active and
                                        psi.machine in machines_used and
                                        sv.planned_start_time.date() == current_time.date() and
                                        sv.planned_start_time <= current_time and
                                        sv.planned_end_time >= current_time and
                                        psi.order.part_number != priority_data.part_number
                                        )[:]

            response_message = (
                f"Priority updated from {old_priority} to {priority_data.new_priority} "
                f"for part {priority_data.part_number}. "
            )

            if current_operations:
                # Get the latest end time of current operations
                latest_operation = max(
                    current_operations,
                    key=lambda x: x[4].planned_end_time  # sv.planned_end_time
                )

                psi, blocking_part, op, machine, sv = latest_operation
                operation_start_time = sv.planned_end_time

                response_message += (
                    f"Machine {machine.work_center.code}-{machine.make} is currently running "
                    f"operation '{op.operation_description}' for part {blocking_part}. "
                    f"Rescheduling will begin after {operation_start_time.strftime('%Y-%m-%d %H:%M:%S')}"
                )
            else:
                operation_start_time = current_time
                response_message += "Rescheduling will begin immediately."

            # Trigger rescheduling
            df = fetch_operations()
            component_quantities = fetch_component_quantities()
            lead_times = fetch_lead_times()

            schedule_df, overall_end_time, overall_time, daily_production, \
                component_status, partially_completed = schedule_operations(
                df, component_quantities, lead_times
            )

            if not schedule_df.empty:
                stored_schedule = store_schedule(schedule_df, component_status)

            return {
                "message": response_message,
                "part_number": priority_data.part_number,
                "old_priority": old_priority,
                "new_priority": priority_data.new_priority,
                "rescheduling_start_time": operation_start_time,
                "status": "success"
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))