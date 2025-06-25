from pony.orm import db_session, select
import pandas as pd
from typing import List
from app.models import Operation, Order, Machine, WorkCenter, Status

@db_session
def fetch_operations():
    """
    Fetch operations with updated database structure
    """
    try:
        operations = select((
            op.operation_description,
            op.machine.id,
            op.machine.make,
            op.ideal_cycle_time,
            op.order.part_number,
            op.id,
            op.operation_number,
            op.setup_time,
            op.order.raw_material.id,  # Changed from op.raw_material to op.order.raw_material
            op.work_center.id,
            op.order.production_order
        ) for op in Operation)

        df = pd.DataFrame(list(operations), columns=[
            "operation", "machine_id", "machine_name", "time", "partno",
            "operation_id", "operation_number", "setup_time", "raw_material_id",
            "work_center_id", "production_order"
        ])

        df['sequence'] = df['operation_number']
        return df

    except Exception as e:
        print(f"Error in fetch_operations: {str(e)}")
        return pd.DataFrame()

@db_session
def insert_operations(operations_data: List[dict]) -> List[dict]:
    """
    Insert new operations into the database
    """
    results = []
    try:
        for op_data in operations_data:
            order = Order.get(part_number=op_data['partno'])
            if not order:
                print(f"Order not found for part number: {op_data['partno']}")
                continue

            machine = Machine.get(id=op_data['machine_id'])
            if not machine:
                print(f"Machine not found with ID: {op_data['machine_id']}")
                continue

            work_center = WorkCenter.get(id=op_data['work_center_id'])
            if not work_center:
                print(f"Work center not found with ID: {op_data['work_center_id']}")
                continue

            new_op = Operation(
                order=order,
                operation_description=op_data['operation'],
                machine=machine,
                ideal_cycle_time=op_data['time'],
                operation_number=op_data.get('sequence', 0),
                setup_time=op_data.get('setup_time', 0),
                work_center=work_center
            )

            results.append({
                "operation_id": new_op.id,
                "partno": order.part_number,
                "operation": new_op.operation_description,
                "machine_id": machine.id,
                "machine_name": machine.make,
                "time": new_op.ideal_cycle_time,
                "sequence": new_op.operation_number,
                "work_center_id": work_center.id
            })

    except Exception as e:
        print(f"Error in insert_operations: {str(e)}")

    return results