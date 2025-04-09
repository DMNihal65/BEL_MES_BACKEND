from datetime import datetime, timedelta, date
import pandas as pd
from typing import Dict, Tuple, List
from decimal import Decimal
from pony.orm import select, db_session
from app.models import Operation, Order, Machine, Status, RawMaterial, Project, InventoryStatus, MachineStatus, \
    PartScheduleStatus


def adjust_to_shift_hours(time: datetime) -> datetime:
    """
    Adjust time to fit within shift hours (9 AM to 5 PM) in IST
    Ensures the time is treated as IST
    """
    # First, ensure the time is treated as IST (it should already be in IST)
    if time.hour < 9:
        return time.replace(hour=9, minute=0, second=0, microsecond=0)
    elif time.hour >= 17:
        return (time + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    return time


@db_session
def schedule_operations(df: pd.DataFrame, component_quantities: Dict[str, int],
                        lead_times: Dict[str, datetime] = None) -> \
        Tuple[pd.DataFrame, datetime, float, Dict, Dict, List[str]]:
    """Main scheduling function that creates a production schedule based on operations data"""

    # COMPREHENSIVE DIAGNOSTIC LOGGING
    print("\n==== SCHEDULING FUNCTION: COMPREHENSIVE DIAGNOSTIC ====")
    print(f"Total Parts Requested: {len(component_quantities)}")
    print(f"Component Quantities: {component_quantities}")
    print(f"Input DataFrame Shape: {df.shape}")
    print(f"Unique Parts in DataFrame: {df['partno'].unique()}")

    # Added: Production order diagnostic
    if 'production_order' in df.columns:
        print(f"Unique Production Orders in DataFrame: {df['production_order'].unique()}")

    # Enhanced Debug Logging
    for partno in component_quantities.keys():
        part_df = df[df['partno'] == partno]
        print(f"\nPart {partno} Debug:")
        print(f"Operations Count: {len(part_df)}")
        if not part_df.empty:
            print(part_df[['operation', 'machine_id', 'sequence', 'time']].to_string())

    if df.empty:
        print("ERROR: Input DataFrame is empty!")
        return pd.DataFrame(), datetime.now(), 0.0, {}, {}, ["Empty input DataFrame"]

    # Gather all production order status information upfront
    # CHANGE: Track by production order instead of part number
    po_status_map = {}
    po_activation_times = {}

    for part_status in PartScheduleStatus.select():
        po_status_map[part_status.production_order] = part_status.status
        # Get activation timestamp for each production order
        if part_status.status == 'active':
            # Convert UTC time from database to IST for scheduling
            ist_offset = timedelta(hours=5, minutes=30)
            activation_time_ist = part_status.updated_at + ist_offset
            po_activation_times[part_status.production_order] = activation_time_ist

    # Create mapping between part numbers and their production orders
    part_to_po_map = {}
    if 'production_order' in df.columns:
        for _, row in df.iterrows():
            if pd.notna(row['production_order']):
                if row['partno'] not in part_to_po_map:
                    part_to_po_map[row['partno']] = []
                if row['production_order'] not in part_to_po_map[row['partno']]:
                    part_to_po_map[row['partno']].append(row['production_order'])

    # Enhanced Filtering for Active Production Orders
    # CHANGE: Filter based on production orders instead of part numbers
    active_production_orders = {
        po: po_status_map.get(po, 'inactive') == 'active'
        for po in df['production_order'].unique() if pd.notna(po)
    }

    # Map from production orders to their part numbers
    po_to_part_map = {}
    if 'production_order' in df.columns:
        for _, row in df.iterrows():
            if pd.notna(row['production_order']):
                po_to_part_map[row['production_order']] = row['partno']

    # Create a component quantities dict based on production orders
    po_quantities = {}
    for po, is_active in active_production_orders.items():
        if is_active and po in po_to_part_map:
            part = po_to_part_map[po]
            if part in component_quantities:
                po_quantities[po] = component_quantities[part]

    print("\n==== ACTIVE PRODUCTION ORDERS DIAGNOSTIC ====")
    for po, is_active in active_production_orders.items():
        if is_active:
            activation_time = po_activation_times.get(po)
            activation_time_str = activation_time.strftime("%Y-%m-%d %H:%M:%S") if activation_time else "None"
            part = po_to_part_map.get(po, "Unknown")

            # Filter operations for this production order
            po_df = df[df['production_order'] == po]

            print(f"Production Order: {po}")
            print(f"  Part Number: {part}")
            print(f"  Quantity: {po_quantities.get(po, 0)}")
            print(f"  Activation Time (IST): {activation_time_str}")
            print(f"  Operations Count: {len(po_df)}")

            if po_df.empty:
                print(f"  WARNING: No operations found for production order {po}")

    # Track skipped production orders with more detailed reasoning
    skipped_pos = [
        f"Skipped {po}: Status {po_status_map.get(po, 'not found')}"
        for po in df['production_order'].unique() if pd.notna(po) and not active_production_orders.get(po, False)
    ]

    print("\n==== SKIPPED PRODUCTION ORDERS ====")
    for skipped in skipped_pos:
        print(skipped)

    # Check if we have any active production orders
    active_pos = [po for po, is_active in active_production_orders.items() if is_active]
    if not active_pos:
        print("CRITICAL: No production orders are marked as active for scheduling")
        return pd.DataFrame(), datetime.now(), 0.0, {}, {}, ["No production orders are marked as active for scheduling"]

    # Filter operations to only include active production orders
    active_df = df[df['production_order'].isin(active_pos)]
    if active_df.empty:
        print("CRITICAL: No operations found for active production orders")
        return pd.DataFrame(), datetime.now(), 0.0, {}, {}, ["No operations found for active production orders"]

    # Get project priorities and lead times
    po_priorities = {}
    po_lead_times = {}
    order_info = {}

    # Gather all order, priority, and lead time information for each production order
    for po in active_pos:
        with db_session:
            order = Order.get(production_order=po)
            if order and order.project:
                po_priorities[po] = order.project.priority if order.project else float('inf')
                if order.project.delivery_date:
                    po_lead_times[po] = order.project.delivery_date

    # Sort production orders by priority (lower number = higher priority)
    sorted_pos = sorted(
        active_pos,
        key=lambda x: po_priorities.get(x, float('inf'))
    )

    # Reorder the dataframe based on sorted production orders
    active_df['sort_order'] = active_df['production_order'].map({po: idx for idx, po in enumerate(sorted_pos)})
    df_sorted = active_df.sort_values(by=['sort_order', 'sequence']).drop('sort_order', axis=1)

    # Fetch raw materials with their InventoryStatus
    raw_materials_query = select((o.production_order, o.raw_material, ist, o.raw_material.available_from)
                                 for o in Order
                                 for ist in InventoryStatus
                                 if o.raw_material and o.raw_material.status == ist)
    raw_materials = {
        production_order: (
            ist.name == 'Available',
            rm.quantity,
            rm.unit,
            rm.available_from
        ) for production_order, rm, ist, available_from in raw_materials_query
    }

    # Fetch machine statuses with their status details
    machine_statuses_query = select((m, ms, s, ms.available_from)
                                    for m in Machine
                                    for ms in m.status
                                    for s in Status if ms.status == s)
    machine_statuses = {
        m.id: {
            'machine_make': m.make,
            'status_id': s.id,
            'status_name': s.name,
            'status_description': s.description,
            'machine_status_description': ms.description,
            'available_from': ms.available_from  # Keep the raw timestamp without automatic timezone conversion
        } for m, ms, s, available_from in machine_statuses_query
    }

    # Group operations by production order instead of part number
    po_operations = {
        po: group.to_dict('records')
        for po, group in df_sorted.groupby('production_order')
    }

    # Get current time in IST as default start date
    ist_offset = timedelta(hours=5, minutes=30)
    default_start_date = datetime.now() + ist_offset
    default_start_date = adjust_to_shift_hours(default_start_date)

    # Find the earliest activation time across all active production orders to use as the global start
    earliest_activation_time = None
    for po in active_pos:
        if po in po_activation_times:
            if earliest_activation_time is None or po_activation_times[po] < earliest_activation_time:
                earliest_activation_time = po_activation_times[po]

    # Use the earliest activation time or default to current time in IST
    global_start_date = adjust_to_shift_hours(
        earliest_activation_time) if earliest_activation_time else default_start_date

    # Log the global start date used for scheduling
    print(f"Global start date (IST): {global_start_date.strftime('%Y-%m-%d %H:%M:%S')}")

    # Initialize machine end times with the earliest start date
    machine_end_times = {machine: global_start_date for machine in df_sorted["machine_id"].unique()}

    schedule = []
    daily_production = {}
    part_status = {}
    partially_completed = []

    def check_machine_status(machine_id: int, time: datetime) -> Tuple[bool, datetime]:
        """Check if a machine is available at a given time"""
        machine_status = machine_statuses.get(machine_id, {})

        print(f"\nChecking Machine ID: {machine_id}")
        print(f"Machine Status Details: {machine_status}")

        if not machine_status:
            print(f"No status found for Machine ID {machine_id}. Assuming unavailable.")
            return False, None

        if machine_status.get('status_name', '').upper() == 'OFF':
            print(f"Machine {machine_id} is OFF")
            return False, None

        available_from = machine_status.get('available_from')
        if available_from:
            # Assume the timestamp is in IST or convert if needed
            # No automatic timezone conversion, use the timestamp as-is
            if time < available_from:
                print(f"Machine {machine_id} not available before {available_from}")
                return False, available_from

        return True, time

    def find_last_available_operation(operations: List[dict], current_time: datetime) -> int:
        """Find the last operation that can be performed in sequence"""
        last_available = -1
        current_op_time = current_time

        for idx, op in enumerate(operations):
            machine_id = op['machine_id']
            machine_available, available_time = check_machine_status(machine_id, current_op_time)

            if not machine_available and available_time is None:
                break

            if available_time:
                current_op_time = available_time

            last_available = idx
            op_time = float(op['time']) * 60
            current_op_time += timedelta(minutes=op_time)

        return last_available

    def schedule_batch_operations(production_order: str, operations: List[dict], quantity: int, start_time: datetime) -> \
    Tuple[
        List[list], int, Dict[int, datetime]]:
        """Schedule operations for a batch of components with precise raw material availability check"""

        # Get part number from operations
        partno = operations[0]['partno'] if operations else None
        if not partno:
            print(f"No part number found in operations for production order {production_order}")
            return [], 0, {}

        # Find the specific order
        try:
            with db_session:
                order = Order.get(production_order=production_order)
                if not order:
                    print(f"No order found for production order {production_order}")
                    return [], 0, {}

                raw_material = order.raw_material
        except Exception as e:
            print(f"Error retrieving order for {production_order}: {str(e)}")
            return [], 0, {}

        if not raw_material:
            print(f"No raw material found for production order {production_order}")
            return [], 0, {}

        # Detailed raw material status check
        raw_material_status = raw_material.status

        # Determine the effective start time
        effective_start_time = start_time

        # Enhanced raw material availability check
        print(f"\nRaw Material Availability Check for Production Order: {production_order}:")
        print(f"Part Number: {partno}")
        print(f"Status: {raw_material_status.name}")
        print(f"Available From: {raw_material.available_from}")
        print(f"Initial Operation Start Time: {effective_start_time}")

        # Comprehensive availability check
        if raw_material_status.name != 'Available':
            print(f"Raw material for Production Order: {production_order} is not in 'Available' status")
            return [], 0, {}

        # If raw material is available from a future time
        if raw_material.available_from:
            # Compare times, ensuring we respect the raw material's availability
            if effective_start_time < raw_material.available_from:
                print(
                    f"Adjusting operation time from {effective_start_time} to raw material available time {raw_material.available_from}")

                # Set effective start time to the raw material's available_from time
                effective_start_time = raw_material.available_from

                # Adjust to the next available shift start if needed
                effective_start_time = adjust_to_shift_hours(effective_start_time)

                print(f"Adjusted operation time after shift hour consideration: {effective_start_time}")

        # Prepare for scheduling
        batch_schedule = []
        operation_time = effective_start_time
        unit_completion_times = {}
        cumulative_pieces = {}
        operation_setup_done = {}

        # Debug print to verify effective start time
        print(f"Final Effective Start Time for Production Order: {production_order}: {effective_start_time}")

        # Rest of the scheduling logic remains the same
        last_available_idx = find_last_available_operation(operations, operation_time)
        if last_available_idx < 0:
            print(f"No available operations found for Production Order: {production_order}")
            return [], 0, {}

        available_operations = operations[:last_available_idx + 1]

        for op_idx, op in enumerate(available_operations):
            machine_id = op['machine_id']
            operation_key = f"{op['operation']}_{machine_id}"

            if operation_key not in cumulative_pieces:
                cumulative_pieces[operation_key] = 0
                operation_setup_done[operation_key] = False

            # Get setup time and cycle time
            with db_session:
                operation = Operation.select(lambda o:
                                             o.order.production_order == production_order and
                                             o.operation_number == op['sequence']).first()

                if not operation:
                    # Fallback to any operation with matching sequence
                    operation = Operation.select(lambda o:
                                                 o.order.part_number == partno and
                                                 o.operation_number == op['sequence']).first()
                    if not operation:
                        continue

                setup_minutes = float(operation.setup_time) * 60
                cycle_minutes = float(operation.ideal_cycle_time) * 60

            current_time = operation_time
            machine_available, available_time = check_machine_status(machine_id, current_time)

            if not machine_available:
                if available_time is None:
                    continue
                current_time = available_time

            current_time = adjust_to_shift_hours(current_time)
            current_time = max(current_time, machine_end_times.get(machine_id, current_time))
            operation_start = current_time

            # Handle setup time
            if not operation_setup_done[operation_key]:
                setup_end = operation_start + timedelta(minutes=setup_minutes)
                shift_end = operation_start.replace(hour=17, minute=0, second=0, microsecond=0)

                if setup_end > shift_end:
                    batch_schedule.append([
                        partno, op['operation'], machine_id,
                        operation_start, shift_end,
                        f"Setup({int((shift_end - operation_start).total_seconds() / 60)}/{setup_minutes}min)",
                        production_order  # Include production order
                    ])

                    next_day = shift_end + timedelta(days=1)
                    next_start = next_day.replace(hour=9, minute=0, second=0, microsecond=0)
                    remaining_setup = setup_minutes - (shift_end - operation_start).total_seconds() / 60

                    while remaining_setup > 0:
                        current_shift_end = next_start.replace(hour=17, minute=0, second=0, microsecond=0)
                        setup_possible = min(remaining_setup, (current_shift_end - next_start).total_seconds() / 60)
                        current_end = next_start + timedelta(minutes=setup_possible)

                        batch_schedule.append([
                            partno, op['operation'], machine_id,
                            next_start, current_end,
                            f"Setup({setup_minutes - remaining_setup + setup_possible}/{setup_minutes}min)",
                            production_order  # Include production order
                        ])

                        remaining_setup -= setup_possible
                        if remaining_setup > 0:
                            next_start = (current_shift_end + timedelta(days=1)).replace(hour=9, minute=0, second=0,
                                                                                         microsecond=0)

                        current_time = current_end
                    operation_start = current_end
                    operation_setup_done[operation_key] = True
                else:
                    batch_schedule.append([
                        partno, op['operation'], machine_id,
                        operation_start, setup_end,
                        f"Setup({setup_minutes}/{setup_minutes}min)",
                        production_order  # Include production order
                    ])
                    operation_start = setup_end
                    current_time = setup_end

                    operation_setup_done[operation_key] = True

            # Process production
            total_processing_time = cycle_minutes * quantity
            processing_end = operation_start + timedelta(minutes=total_processing_time)
            shift_end = operation_start.replace(hour=17, minute=0, second=0, microsecond=0)

            if processing_end > shift_end:
                # Split processing across shifts
                work_minutes_today = (shift_end - operation_start).total_seconds() / 60
                completion_ratio = work_minutes_today / total_processing_time if total_processing_time > 0 else 0
                pieces_today = int(quantity * completion_ratio)

                new_cumulative = min(cumulative_pieces[operation_key] + pieces_today, quantity)
                if work_minutes_today > 0:
                    batch_schedule.append([
                        partno, op['operation'], machine_id,
                        operation_start, shift_end,
                        f"Process({new_cumulative}/{quantity}pcs)",
                        production_order  # Include production order
                    ])
                    cumulative_pieces[operation_key] = new_cumulative

                remaining_time = total_processing_time - work_minutes_today
                remaining_pieces = quantity - new_cumulative

                next_day = shift_end + timedelta(days=1)
                next_start = next_day.replace(hour=9, minute=0, second=0, microsecond=0)

                while remaining_time > 0:
                    current_shift_end = next_start.replace(hour=17, minute=0, second=0, microsecond=0)
                    work_possible = min(remaining_time, (current_shift_end - next_start).total_seconds() / 60)
                    current_end = next_start + timedelta(minutes=work_possible)

                    shift_completion_ratio = work_possible / remaining_time
                    pieces_this_shift = min(remaining_pieces,
                                            remaining_pieces if work_possible >= remaining_time
                                            else int(remaining_pieces * shift_completion_ratio))

                    new_cumulative = min(cumulative_pieces[operation_key] + pieces_this_shift, quantity)

                    batch_schedule.append([
                        partno, op['operation'], machine_id,
                        next_start, current_end,
                        f"Process({new_cumulative}/{quantity}pcs)",
                        production_order  # Include production order
                    ])

                    cumulative_pieces[operation_key] = new_cumulative
                    remaining_pieces = quantity - new_cumulative
                    remaining_time -= work_possible

                    if remaining_time > 0:
                        next_start = (current_shift_end + timedelta(days=1)).replace(hour=9, minute=0, second=0,
                                                                                     microsecond=0)

                    current_time = current_end
                    machine_end_times[machine_id] = current_end
            else:
                cumulative_pieces[operation_key] = quantity
                batch_schedule.append([
                    partno, op['operation'], machine_id,
                    operation_start, processing_end,
                    f"Process({quantity}/{quantity}pcs)",
                    production_order  # Include production order
                ])
                current_time = processing_end
                machine_end_times[machine_id] = processing_end

            if op_idx == len(available_operations) - 1:
                for unit_number in range(1, quantity + 1):
                    unit_completion_times[unit_number] = current_time

            operation_time = max(machine_end_times[machine_id], operation_time)

        return batch_schedule, len(available_operations), unit_completion_times

    # Create a list of production orders to schedule
    sorted_schedule_items = []
    for po in sorted_pos:
        if po not in po_operations:
            continue

        # Get partno from first operation
        operations = po_operations[po]
        if not operations:
            continue

        partno = operations[0]['partno']

        # Add the production order to our schedule list
        sorted_schedule_items.append({
            'production_order': po,
            'partno': partno,
            'priority': po_priorities.get(po, float('inf')),
            'lead_time': po_lead_times.get(po),
            'operations': operations,
            'quantity': po_quantities.get(po, 0)
        })

    # Main scheduling loop using the sorted items with production orders
    if sorted_schedule_items:
        for schedule_item in sorted_schedule_items:
            production_order = schedule_item['production_order']
            partno = schedule_item['partno']
            operations = schedule_item['operations']
            quantity = schedule_item['quantity']
            priority = schedule_item['priority']
            lead_time = schedule_item['lead_time']

            # Use production order-specific activation time if available, otherwise use global start date
            po_start_time = po_activation_times.get(production_order, global_start_date)
            po_start_time = adjust_to_shift_hours(po_start_time)

            print(
                f"Scheduling Production Order: {production_order} (Part: {partno}) with activation time (IST): {po_start_time.strftime('%Y-%m-%d %H:%M:%S')}")

            # Schedule operations for this production order
            batch_schedule, completed_ops, unit_completion_times = schedule_batch_operations(
                production_order, operations, quantity, po_start_time
            )

            if batch_schedule:
                schedule.extend(batch_schedule)
                latest_completion_time = max(unit_completion_times.values()) if unit_completion_times else None

                # Track status for this production order
                part_status[production_order] = {
                    'production_order': production_order,
                    'partno': partno,
                    'scheduled_end_time': latest_completion_time,
                    'priority': priority,
                    'lead_time': lead_time,
                    'completed_quantity': len(unit_completion_times),
                    'total_quantity': quantity,
                    'lead_time_provided': lead_time is not None,
                    'start_time': po_start_time
                }

                # Calculate lead time difference for monitoring
                if lead_time and latest_completion_time:
                    time_difference = (lead_time - latest_completion_time).days
                    part_status[production_order]['lead_time_difference'] = time_difference

                # Update daily production tracking
                if production_order not in daily_production:
                    daily_production[production_order] = {
                        'partno': partno,
                        'dates': {}
                    }

                for unit_num, completion_time in unit_completion_times.items():
                    completion_day = completion_time.date()
                    if completion_day not in daily_production[production_order]['dates']:
                        daily_production[production_order]['dates'][completion_day] = 0
                    daily_production[production_order]['dates'][completion_day] += 1

                if completed_ops < len(operations):
                    partially_completed.append(
                        f"PO: {production_order} (Part: {partno}): Completed {completed_ops}/{len(operations)} operation types for {quantity} units")

    # Create schedule dataframe with production order information
    if not schedule:
        return pd.DataFrame(), global_start_date, 0.0, daily_production, part_status, partially_completed

    schedule_df = pd.DataFrame(
        schedule,
        columns=["partno", "operation", "machine_id", "start_time", "end_time", "quantity", "production_order"]
    )

    if schedule_df.empty:
        return schedule_df, global_start_date, 0.0, daily_production, part_status, partially_completed

    overall_end_time = max(schedule_df['end_time'])
    overall_time = (overall_end_time - global_start_date).total_seconds() / 60

    return schedule_df, overall_end_time, overall_time, daily_production, part_status, partially_completed