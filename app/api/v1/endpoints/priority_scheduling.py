from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime
import pandas as pd
from pony.orm import db_session, select, commit

from app.algorithm.scheduling import schedule_operations
from app.models import Operation, Order, PartScheduleStatus, Project, PlannedScheduleItem, ScheduleVersion

router = APIRouter(prefix="/test", tags=["test"])


class PriorityUpdate(BaseModel):
    part_number: str
    priority: int
    maintain_in_progress: bool = True  # Whether to respect in-progress operations


class PriorityResponse(BaseModel):
    part_number: str
    old_priority: int
    new_priority: int
    status: str
    message: str


class OperationStatus(BaseModel):
    part_number: str
    priority: int
    status: str  # 'not_started', 'in_progress', 'completed'
    current_operation: Optional[int]
    total_operations: int
    start_time: Optional[datetime]
    end_time: Optional[datetime]


@router.get("/part-status", response_model=List[OperationStatus])
@db_session
def get_part_status():
    """Get the current status of all active parts and their operations"""
    parts = select(p for p in PartScheduleStatus if p.status == 'active')

    result = []
    for part in parts:
        order = Order.select(lambda o: o.part_number == part.part_number).first()
        if not order:
            continue

        # Get schedule items for this part
        schedule_items = select(psi for psi in PlannedScheduleItem if psi.order == order)

        # Count total schedule items for operations
        scheduled_operations_count = schedule_items.count()

        # Determine status based on schedule information, not production logs
        status = "not_started"
        current_op = None
        start_time = None
        end_time = None

        if scheduled_operations_count > 0:
            # Check if all operations are scheduled
            if scheduled_operations_count == order.total_operations:
                status = "completed"
            else:
                status = "in_progress"

            # Get the current/latest operation from schedule
            latest_item = schedule_items.order_by(lambda psi: psi.initial_end_time).first()
            if latest_item and latest_item.current_version:
                current_version = select(
                    sv for sv in ScheduleVersion
                    if sv.schedule_item == latest_item and sv.version_number == latest_item.current_version
                ).first()

                if current_version:
                    current_op = latest_item.operation.operation_number
                    start_time = current_version.planned_start_time
                    end_time = current_version.planned_end_time

        result.append(OperationStatus(
            part_number=part.part_number,
            priority=order.project.priority if order.project else 999,
            status=status,
            current_operation=current_op,
            total_operations=order.total_operations,
            start_time=start_time,
            end_time=end_time
        ))

    return result


@router.post("/update-priority", response_model=PriorityResponse)
@db_session
def update_part_priority(update: PriorityUpdate):
    """Update the priority of a part and reschedule operations"""
    try:
        order = Order.select(lambda o: o.part_number == update.part_number).first()
        if not order:
            raise HTTPException(status_code=404, detail=f"Part {update.part_number} not found")

        # Get current priority
        old_priority = order.project.priority if order.project else 999

        # Check if part is active for scheduling
        part_status = PartScheduleStatus.select(lambda p: p.part_number == update.part_number).first()
        if not part_status or part_status.status != 'active':
            part_status_message = "Part is not active for scheduling"
            raise HTTPException(status_code=400, detail=f"Cannot update priority. {part_status_message}")
        else:
            part_status_message = "Part is active for scheduling"

        # Check if there are scheduled operations for this part
        schedule_items = select(psi for psi in PlannedScheduleItem if psi.order == order)

        # Determine if schedule exists and status
        if schedule_items.count() > 0:
            # If all operations are scheduled, consider it completed
            if schedule_items.count() == order.total_operations:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot update priority for part {update.part_number}. All operations are already scheduled."
                )

            # If some operations are scheduled, consider it in progress
            raise HTTPException(
                status_code=400,
                detail=f"Cannot update priority for part {update.part_number}. Scheduling is already in progress."
            )

        # Check if the requested priority is already assigned to an in-progress or completed part
        # Get all parts with the same priority
        parts_with_same_priority = select(o for o in Order
                                          if o.project and o.project.priority == update.priority
                                          and o.part_number != update.part_number)

        for existing_part in parts_with_same_priority:
            # Check if this part has scheduled operations
            existing_schedule_items = select(psi for psi in PlannedScheduleItem if psi.order == existing_part)

            if existing_schedule_items.count() > 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot use priority {update.priority}. It is already assigned to part {existing_part.part_number} which is in-progress or completed."
                )

        # Update priority
        if not order.project:
            # Create a new project if none exists
            project = Project(
                name=f"Project for {update.part_number}",
                priority=update.priority,
                start_date=datetime.now(),
                end_date=datetime.now(),
                delivery_date=datetime.now()
            )
            order.project = project
        else:
            order.project.priority = update.priority

        # Commit the changes before scheduling
        commit()

        # Trigger operations scheduling (not rescheduling)
        schedule_operations_for_part(order.part_number)

        return PriorityResponse(
            part_number=update.part_number,
            old_priority=old_priority,
            new_priority=update.priority,
            status="success",
            message=f"Priority updated. {part_status_message}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error during priority update: {str(e)}")


@db_session
def schedule_operations_for_part(part_number):
    """Schedule operations for a specific part"""
    try:
        # Get all operations data
        operations_data = get_operations_data(part_number)

        # Get component quantities
        quantities = get_component_quantities(part_number)

        # Schedule operations
        schedule_df, end_time, duration, daily_prod, part_status, partial = schedule_operations(
            operations_data, quantities
        )

        # Save the new schedule to the database
        save_schedule_to_db(schedule_df)

        return {
            "message": "Scheduling completed successfully",
            "end_time": end_time,
            "duration_minutes": duration,
            "parts_scheduled": len(part_status),
            "partially_completed": partial
        }
    except Exception as e:
        print(f"Error scheduling operations: {e}")
        raise


def get_operations_data(part_number=None):
    """Get operations data for scheduling, optionally filtered by part number"""
    if part_number:
        operations = select((
                                o.order.part_number,
                                o.operation_number,
                                o.machine.id,
                                o.setup_time,
                                o.ideal_cycle_time
                            ) for o in Operation if o.order.part_number == part_number)
    else:
        operations = select((
                                o.order.part_number,
                                o.operation_number,
                                o.machine.id,
                                o.setup_time,
                                o.ideal_cycle_time
                            ) for o in Operation)

    # Convert to DataFrame
    df = pd.DataFrame([
        {
            "partno": part_number,
            "sequence": op_number,
            "machine_id": machine_id,
            "time": float(cycle_time),
            "setup": float(setup_time),
            "operation": f"OP{op_number}"
        }
        for part_number, op_number, machine_id, setup_time, cycle_time in operations
    ])

    return df


def get_component_quantities(part_number=None):
    """Get required quantities for active parts, optionally filtered by part number"""
    if part_number:
        active_parts = select((
                                  p.part_number,
                                  o.required_quantity
                              ) for p in PartScheduleStatus for o in Order
                              if p.part_number == o.part_number and p.status == 'active'
                              and p.part_number == part_number)
    else:
        active_parts = select((
                                  p.part_number,
                                  o.required_quantity
                              ) for p in PartScheduleStatus for o in Order
                              if p.part_number == o.part_number and p.status == 'active')

    return {part: qty for part, qty in active_parts}


@db_session
def save_schedule_to_db(schedule_df):
    """Save the scheduled operations to the database"""
    if schedule_df.empty:
        return

    try:
        # Process each scheduled operation
        for _, row in schedule_df.iterrows():
            part_no = row["partno"]
            op_name = row["operation"]
            machine_id = row["machine_id"]
            start_time = row["start_time"]
            end_time = row["end_time"]

            # Extract operation number from operation name (e.g., "OP10" -> 10)
            op_num = int(op_name.replace("OP", ""))

            # Get the order and operation
            order = Order.select(lambda o: o.part_number == part_no).first()
            if not order:
                continue

            operation = Operation.select(
                lambda o: o.order == order and o.operation_number == op_num
            ).first()
            if not operation:
                continue

            # Get the machine
            machine = None
            try:
                from app.models import Machine
                machine = Machine[machine_id]
                if not machine:
                    continue
            except:
                continue

            # Parse quantity info from the row
            qty_info = row["quantity"]
            completed_qty = 0
            total_qty = 0

            try:
                if "Process" in qty_info:
                    # Extract quantities from format like "Process(10/20pcs)"
                    qty_parts = qty_info.replace("Process(", "").replace("pcs)", "").split("/")
                    completed_qty = int(qty_parts[0])
                    total_qty = int(qty_parts[1])
            except Exception:
                pass

            # Find existing schedule item or create new one
            schedule_item = PlannedScheduleItem.select(
                lambda psi: psi.order == order and
                            psi.operation == operation and
                            psi.machine == machine
            ).first()

            if not schedule_item:
                schedule_item = PlannedScheduleItem(
                    order=order,
                    operation=operation,
                    machine=machine,
                    initial_start_time=start_time,
                    initial_end_time=end_time,
                    total_quantity=total_qty,
                    remaining_quantity=max(0, total_qty - completed_qty),
                    status="scheduled",
                    current_version=1
                )
            else:
                # Update the existing schedule item
                schedule_item.initial_start_time = start_time
                schedule_item.initial_end_time = end_time
                schedule_item.total_quantity = total_qty
                schedule_item.remaining_quantity = max(0, total_qty - completed_qty)

            # Create a new version
            version_num = (schedule_item.current_version or 0) + 1
            schedule_item.current_version = version_num

            # Create the new schedule version
            new_version = ScheduleVersion(
                schedule_item=schedule_item,
                version_number=version_num,
                planned_start_time=start_time,
                planned_end_time=end_time,
                planned_quantity=total_qty,
                completed_quantity=completed_qty,
                remaining_quantity=max(0, total_qty - completed_qty),
                is_active=True
            )
    except Exception as e:
        print(f"Error saving schedule to database: {e}")
        raise