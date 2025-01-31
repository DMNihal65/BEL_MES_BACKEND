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




def extract_quantity(quantity_str: str) -> int:
    """
    Extract quantity from different format strings like:
    - "Process(10/10pcs)"
    - "Setup(18.0min)"
    """
    try:
        # If it's a process quantity (e.g., "Process(10/10pcs)")
        if "Process" in quantity_str:
            match = re.search(r'Process\((\d+)/(\d+)pcs\)', quantity_str)
            if match:
                return int(match.group(2))  # Return the total quantity

        # If it's a setup time (e.g., "Setup(18.0min)")
        elif "Setup" in quantity_str:
            match = re.search(r'Setup\(([\d.]+)/([\d.]+)min\)', quantity_str)
            if match:
                return 1  # For setup operations, quantity is 1

        # Default case - try to extract first number
        numbers = re.findall(r'\d+', quantity_str)
        if numbers:
            return int(numbers[0])

        return 1  # Default quantity if no pattern matches

    except Exception as e:
        print(f"Error parsing quantity string: {quantity_str}, Error: {str(e)}")
        return 1  # Default to 1 if parsing fails


@db_session
def store_schedule(schedule_df, component_status):
    """Store the generated schedule in the database"""
    try:
        stored_items = []

        # Debug: Print column names and sample data
        print("Schedule DataFrame columns:", schedule_df.columns.tolist())
        print("Sample row data:", schedule_df.iloc[0].to_dict())

        for _, row in schedule_df.iterrows():
            print(f"\nProcessing row: {row.to_dict()}")

            # Get related entities from master tables
            order = Order.get(part_number=row['partno'])
            if not order:
                print(f"Order not found for part number: {row['partno']}")
                continue

            # Get all matching operations
            matching_operations = Operation.select(
                lambda op: op.order == order and
                           op.operation_description == row['operation']
            )[:]

            if not matching_operations:
                print(f"No operations found for: {row['operation']} in order {row['partno']}")
                continue

            if len(matching_operations) > 1:
                print(f"\nMultiple operations found for Order: {row['partno']}, Operation: {row['operation']}")
                for op in matching_operations:
                    print(
                        f"- Operation ID: {op.id}, Number: {op.operation_number}, Description: {op.operation_description}")
                print("Using first matching operation.")

            operation = matching_operations[0]  # Use the first matching operation
            if not operation:
                print(f"Operation not found: {row['operation']} for order {row['partno']}")
                continue

            machine = Machine[row['machine_id']]
            if not machine:
                print(f"Machine not found with ID: {row['machine_id']}")
                continue

            # Extract quantity based on the format
            quantity = extract_quantity(row['quantity'])
            print(f"Extracted quantity: {quantity} from string: {row['quantity']}")

            # Create PlannedScheduleItem with proper FK relationships
            schedule_item = PlannedScheduleItem(
                order=order,
                operation=operation,
                machine=machine,
                initial_start_time=row['start_time'],
                initial_end_time=row['end_time'],
                total_quantity=quantity,
                remaining_quantity=quantity,
                status='scheduled',
                current_version=1
            )

            # Create initial ScheduleVersion
            schedule_version = ScheduleVersion(
                schedule_item=schedule_item,
                version_number=1,
                planned_start_time=row['start_time'],
                planned_end_time=row['end_time'],
                planned_quantity=quantity,
                remaining_quantity=quantity,
                is_active=True
            )

            stored_items.append({
                'schedule_item_id': schedule_item.id,
                'version_id': schedule_version.id,
                'quantity': quantity
            })

        return stored_items

    except Exception as e:
        print(f"Error storing schedule: {str(e)}")
        raise e





# Modified schedule endpoint
@router.get("/schedule-batch/", response_model=ScheduleResponse)
async def schedule():
    """Generate schedule for active parts and store in database"""
    try:
        with db_session:
            # Get database counts for debugging
            ops_count = Operation.select().count()
            orders_count = Order.select().count()
            print(f"Database counts - Operations: {ops_count}, Orders: {orders_count}")

        # Get scheduling data
        df = fetch_operations()
        component_quantities = fetch_component_quantities()
        lead_times = fetch_lead_times()

        # Get schedule based on active parts
        schedule_df, overall_end_time, overall_time, daily_production, \
            component_status, partially_completed = schedule_operations(
            df, component_quantities, lead_times
        )

        # Store the generated schedule
        stored_schedule = None
        if not schedule_df.empty:
            with db_session:
                stored_schedule = store_schedule(schedule_df, component_status)
                print(f"Successfully stored {len(stored_schedule)} schedule items")

        # Convert schedule to response format with stored IDs
        scheduled_operations = []
        if not schedule_df.empty:
            with db_session:
                # Get machine details
                machine_details = {
                    machine.id: f"{machine.work_center.code}-{machine.make}"
                    for machine in Machine.select()
                }

                # Get orders mapping
                orders_map = {
                    order.part_number: order.production_order
                    for order in Order.select()
                }

            scheduled_operations = [
                ScheduledOperation(
                    component=row['partno'],
                    description=row['operation'],
                    machine=machine_details.get(row['machine_id'], f"Machine-{row['machine_id']}"),
                    start_time=row['start_time'],
                    end_time=row['end_time'],
                    quantity=row['quantity'],
                    production_order=orders_map.get(row['partno'], '')
                ) for _, row in schedule_df.iterrows()
            ]

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