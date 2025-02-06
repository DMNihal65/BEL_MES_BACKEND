from fastapi import APIRouter, HTTPException
from pony.orm import db_session, select
from app.schemas.scheduled import ScheduledOperation, ScheduleResponse
from app.models import Order, Operation, Machine, PartScheduleStatus, PlannedScheduleItem, ScheduleVersion
from app.crud.operation import fetch_operations
from app.crud.component_quantities import fetch_component_quantities
from app.crud.leadtime import fetch_lead_times
from app.algorithm.scheduling import schedule_operations
import re

router = APIRouter(prefix="/scheduling", tags=["scheduling"])


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