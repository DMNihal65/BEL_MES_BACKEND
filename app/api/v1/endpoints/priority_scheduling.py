from fastapi import APIRouter, HTTPException
from pony.orm import db_session, select
from datetime import datetime, timedelta
from typing import Dict, Optional
from pydantic import BaseModel

from app.models import Order, Operation, PlannedScheduleItem, Project


class PriorityUpdate(BaseModel):
    part_number: str
    new_priority: int


router = APIRouter(prefix="/api/v1/priority", tags=["priority"])


@router.post("/update-priority", response_model=Dict)
async def update_priority(priority_data: PriorityUpdate):
    """Update priority of a part without triggering immediate rescheduling"""
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

            # Set a flag to indicate this is for schedule_batch only, not for rescheduling
            if hasattr(project, 'schedule_batch_flag'):
                project.schedule_batch_flag = True

            # Check if schedule_batch_priority field exists and update it
            if hasattr(project, 'schedule_batch_priority'):
                project.schedule_batch_priority = priority_data.new_priority

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
                    f"Changes will be applied in the next schedule batch run."
                )
            else:
                operation_start_time = current_time
                response_message += "Changes will be applied in the next schedule batch run. This will not affect rescheduling."

            return {
                "message": response_message,
                "part_number": priority_data.part_number,
                "old_priority": old_priority,
                "new_priority": priority_data.new_priority,
                "priority_updated_at": current_time,
                "affects_rescheduling": False,
                "status": "success"
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))