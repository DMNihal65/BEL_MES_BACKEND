from fastapi import FastAPI, File, UploadFile, APIRouter, HTTPException, Query
from pony.orm import db_session, select, commit, count, get
from datetime import datetime, timedelta
from typing import List, Optional
import PyPDF2
import io
import re
import json
from app.database.connection import db
from app.models import (
    WorkCenter, Machine, Project, Order, Operation,
    ProcessPlan, Document, ToolList, JigsAndFixturesList,
    Unit, RawMaterial, InventoryStatus, PartScheduleStatus, MachineStatus, MachineShift, Status
)
from app.schemas.planning import CreateOperationRequest, CreateOrderRequest, OrderUpdateRequest, OperationUpdateRequest, \
    SaveDataRequest, ProjectPriorityUpdateRequest, OrderUpdate_Response, OrderUpdate_Request, CreateOrderRequest_new

router = APIRouter(prefix="/api/v1/planning", tags=["planning"])


def extract_oarc_details(pdf_content):
    # Read PDF
    pdf_reader = PyPDF2.PdfReader(pdf_content)
    text = ""
    for page in pdf_reader.pages:
        text += page.extract_text() + "\n"

    # Initialize dictionary to store extracted data
    data = {
        "Project Name": "",
        "Sale Order": "",
        "Part No": "",
        "Part Desc": "",
        "Required Qty": "",
        "Plant": "",
        "WBS": "",
        "Rtg Seq No": "",
        "Sequence No": "",
        "Launched Qty": "",
        "Prod Order No": "",
        "Operations": [],
        "Document Verification": {},
        "Raw Materials": []
    }

    # Extract header information using specific patterns
    # Project Name and Part No
    project_match = re.search(r"Project Name\s*:([^:]+)Part No\s*:([^W]+)WBS\s*:\s*([^\n]+)", text)
    if project_match:
        data["Project Name"] = project_match.group(1).strip()
        data["Part No"] = project_match.group(2).strip()
        data["WBS"] = project_match.group(3).strip()

    # Sale order and Part Desc
    sale_match = re.search(r"Sale order\s*:([^:]+)Part Desc\s*:([^T]+)", text)
    if sale_match:
        data["Sale Order"] = sale_match.group(1).strip()
        data["Part Desc"] = sale_match.group(2).strip()

    # Plant and sequence numbers
    plant_match = re.search(r"Plant\s*:([^R]+)Rtg\s+Seq\s*No\s*:([^S]+)Sequence\s*No\s*:([^\n]+)", text)
    if plant_match.group(1):
        data["Plant"] = plant_match.group(1).strip()
        data["Rtg Seq No"] = plant_match.group(2).strip()
        data["Sequence No"] = plant_match.group(3).strip()
    else:
        data["Plant"] = plant_match.group(4).strip()
        data["Rtg Seq No"] = plant_match.group(5).strip()
        data["Sequence No"] = plant_match.group(6).strip()

    # Required Qty, Launched Qty, and Prod Order No
    qty_match = re.search(
        r"Required\s*Qty\s*:\s*([^\n]+?)\s*"
        r"Launched\s*Qty\s*:\s*([^\n]+?)\s*"
        r"Prod\s*Order\s*No\s*:\s*([^\n]+)",
        text
    )

    if qty_match:
        if qty_match.group(1):
            data["Required Qty"] = qty_match.group(1).strip()
            data["Launched Qty"] = qty_match.group(2).strip()
            data["Prod Order No"] = qty_match.group(3).strip()
        else:
            data["Required Qty"] = qty_match.group(4).strip()
            data["Launched Qty"] = qty_match.group(5).strip()
            data["Prod Order No"] = qty_match.group(6).strip()

    # Extract operations
    lines = text.split('\n')
    operation_started = False
    current_operation = None
    long_text_started = False

    for i, line in enumerate(lines):
        line = line.strip()
        if not line or line.startswith('_'):
            continue

        # Check if we've reached the operations section
        if "Oprn" in line and "Operation" in line:
            operation_started = True
            continue

        if operation_started:
            # Try to match operation row
            op_match = re.match(
                r'(\d{4})\s+([A-Z0-9-]+)\s+(\d+\.?\d*)\s+(\d+\.?\d*)\s+(\d+)\s+(\d+)\s+(\d+\.?\d*)\s*(\d*)', line)

            if op_match:
                if current_operation:
                    data["Operations"].append(current_operation)

                # Get the next line for additional plant info and operation
                next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
                next_next_line = lines[i + 2].strip() if i + 2 < len(lines) else ""

                # Extract plant number and operation description
                plant_number = ""
                operation_desc = ""

                if next_line:
                    # Check if next line contains a plant number
                    plant_match = re.match(r'^(\d+)\s*(.*)', next_line)
                    if plant_match:
                        plant_number = plant_match.group(1)
                        if plant_match.group(2):  # If there's text after the number
                            operation_desc = plant_match.group(2)
                        elif next_next_line and not next_next_line.startswith("Long Text"):
                            operation_desc = next_next_line
                    else:
                        operation_desc = next_line

                current_operation = {
                    "Oprn No": op_match.group(1),
                    "Wc/Plant": op_match.group(2),
                    "Plant Number": plant_number,
                    "Operation": operation_desc,
                    "Setup Time": op_match.group(3),
                    "Per Pc Time": op_match.group(4),
                    "Jmp Qty": op_match.group(5),
                    "Tot Qty": op_match.group(6),
                    "Allowed Time": op_match.group(7),
                    "Confirm No": op_match.group(8) if op_match.group(8) else "",
                    "Long Text": ""
                }
            elif current_operation:
                if "Long Text:" in line:
                    long_text_started = True
                    continue

                if long_text_started:
                    if current_operation["Long Text"]:
                        current_operation["Long Text"] += "\n" + line
                    else:
                        current_operation["Long Text"] = line

    # Add the last operation if exists
    if current_operation:
        data["Operations"].append(current_operation)

    raw_materials_started = False
    raw_material_pattern = r'(\d{4})\s+(\w+)\s+([\w\s\-\.]+)\s+([\d\.]+)\s+(\w+)\s+([\d\.]+)'

    for i, line in enumerate(lines):
        line = line.strip()

        # Check if we've reached the raw materials section
        if "Item" in line and ("Child Part No" in line or "Child" in line):
            raw_materials_started = True
            continue

        if raw_materials_started and not line.startswith('_'):
            # Try to match raw material row
            raw_match = re.match(raw_material_pattern, line)
            if raw_match:
                raw_material = {
                    "Sl.No": raw_match.group(1),
                    "Child Part No": raw_match.group(2),
                    "Description": raw_match.group(3).strip(),
                    "Qty Per Set": raw_match.group(4),
                    "UoM": raw_match.group(5),
                    "Total Qty": raw_match.group(6)
                }
                data["Raw Materials"].append(raw_material)

        # End raw materials section if we hit another section
        if raw_materials_started and line.startswith('SPECIAL NOTE'):
            raw_materials_started = False

    print(f"\n\n{'$' * 50}\n{data}\n{'$' * 50}\n\n")

    return data


@db_session
def save_to_database(data):
    try:
        # Check if order exists
        existing_order = Order.get(production_order=data["Prod Order No"])
        if existing_order:
            raise HTTPException(status_code=400, detail=f"Production order '{data['Prod Order No']}' already exists.")

        # Always create a new project instead of reusing existing ones
        # Get current max priority
        max_priority = select(max(p.priority) for p in Project).first() or 0
        project = Project(
            name=data["Project Name"],
            priority=max_priority + 1,  # Auto-increment
            start_date=datetime.now(),
            end_date=datetime.now(),
            delivery_date=datetime.now()
        )

        # Get or create default inventory status
        default_status = InventoryStatus.get(name="Available")
        if not default_status:
            default_status = InventoryStatus(
                name="Available",
                description="Material is available for use"
            )

        # Get or create default unit
        default_unit = Unit.get(name="EA")
        if not default_unit:
            default_unit = Unit(name="EA")  # EA for "Each"

        # Create raw material - Modified part
        if "Raw Materials" in data and data["Raw Materials"] and len(data["Raw Materials"]) > 0:
            # Create raw material from provided data
            unit = Unit.get(name=data["Raw Materials"][0]["UoM"])
            if not unit:
                unit = Unit(name=data["Raw Materials"][0]["UoM"])

            raw_material = RawMaterial(
                child_part_number=data["Raw Materials"][0]["Child Part No"],
                description=data["Raw Materials"][0]["Description"],
                quantity=float(data["Raw Materials"][0]["Total Qty"]),
                unit=unit,
                status=default_status,
                available_from=datetime(2024, 1, 2, 9, 0)  # Added hardcoded available_from date to match create_order
            )
        else:
            # Create default raw material since it's required
            raw_material = RawMaterial(
                child_part_number="DEFAULT-" + data["Part No"],
                description="Default raw material for " + data["Part Desc"],
                quantity=0.0,
                unit=default_unit,
                status=default_status,
                available_from=datetime(2024, 1, 2, 9, 0)
            )

        # Create master order
        master_order = Order(
            production_order=data["Prod Order No"],
            sale_order=data["Sale Order"],
            wbs_element=data["WBS"],
            part_number=data["Part No"],
            part_description=data["Part Desc"],
            total_operations=len(data["Operations"]),
            required_quantity=int(float(data["Required Qty"])),
            launched_quantity=int(float(data["Launched Qty"])),
            project=project,
            plant_id=data["Plant"],
            raw_material=raw_material
        )

        # Create initial 'inactive' status for scheduling - FIX: Use both part_number and production_order
        part_status = PartScheduleStatus.get(part_number=data["Part No"], production_order=data["Prod Order No"])
        if not part_status:
            PartScheduleStatus(
                part_number=data["Part No"],
                production_order=data["Prod Order No"],
                status='inactive'  # Default to inactive when OARC is uploaded
            )

        # Create documents
        for doc_type, doc_info in data["Document Verification"].items():
            Document(
                order=master_order,
                document_name=doc_type,
                type=doc_type,
                version=doc_info.get("Revision", "--") if isinstance(doc_info, dict) else "--",
                upload_date=datetime.now()
            )

        # Get or create default machine status
        default_status_on = Status.get(name="ON")
        if not default_status_on:
            default_status_on = Status(
                name="ON",
                description="Machine is operational"
            )

        # Create operations and work centers
        for op in data["Operations"]:
            # Check if work center exists
            work_center = WorkCenter.get(code=op["Wc/Plant"])
            if not work_center:
                work_center = WorkCenter(
                    code=op["Wc/Plant"],
                    plant_id=op["Plant Number"] or "0",
                    work_center_name=op["Operation"],
                    description=op["Operation"]
                )

            # Get all existing machines for this work center
            existing_machines = select(m for m in Machine if m.work_center == work_center)[:]

            # If no machines exist for this work center, create a default one
            if not existing_machines:
                # Create default machine
                machine = Machine(
                    work_center=work_center,
                    type="Default",
                    make="Default",
                    model="Default",
                    year_of_installation=2024,  # Default year
                    cnc_controller="Default Controller",
                    cnc_controller_series="Default Series",
                    calibration_date=datetime(2024, 1, 1),  # Default calibration date
                    last_maintenance_date=datetime(2024, 1, 1)  # Default maintenance date
                )

                # Create default machine status
                MachineStatus(
                    machine=machine,
                    status=default_status_on,
                    description="Machine is operational",
                    available_from=datetime(2025, 1, 21, 11, 41, 20, 417587)  # Hardcoded as requested
                )

                # Create default machine shift
                MachineShift(
                    machine=machine,
                    shift_start=datetime(2024, 1, 1, 9, 0),  # 9 AM start
                    shift_end=datetime(2024, 1, 1, 17, 0),  # 5 PM end
                    is_active=True
                )
            else:
                # Use the first existing machine
                machine = existing_machines[0]

                # Check if machine status exists, if not create it
                existing_status = select(ms for ms in MachineStatus if ms.machine == machine).first()
                if not existing_status:
                    MachineStatus(
                        machine=machine,
                        status=default_status_on,
                        description="Machine is operational",
                        available_from=datetime(2025, 1, 21, 11, 41, 20, 417587)  # Hardcoded as requested
                    )

            # Create a new operation specific to this order
            operation = Operation(
                order=master_order,
                work_center=work_center,
                machine=machine,
                operation_number=int(op["Oprn No"]),
                operation_description=op["Operation"],
                setup_time=float(op["Setup Time"]),
                ideal_cycle_time=float(op["Per Pc Time"])
            )

        return master_order

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi import HTTPException
from pony.orm import db_session, select

# Create a thread pool executor for database operations
executor = ThreadPoolExecutor(max_workers=10)


@router.get("/all_orders")
async def get_all_orders():
    try:
        # Run the database query in a thread pool to avoid blocking
        def get_orders_sync():
            with db_session:
                orders = select(o for o in Order)[:]
                return [
                    {
                        "id": order.id,
                        "production_order": order.production_order,
                        "sale_order": order.sale_order,
                        "wbs_element": order.wbs_element,
                        "part_number": order.part_number,
                        "part_description": order.part_description,
                        "total_operations": order.total_operations,
                        "required_quantity": order.required_quantity,
                        "launched_quantity": order.launched_quantity,
                        "plant_id": order.plant_id,
                        "project": {
                            "id": order.project.id,
                            "name": order.project.name,
                            "priority": order.project.priority,
                            "delivery_date": order.project.delivery_date
                        } if order.project else None
                    }
                    for order in orders
                ]

        # Execute the database operation in a thread pool
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(executor, get_orders_sync)
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search_order")
async def search_order(
        part_number: Optional[str] = Query(None, min_length=1),
        part_description: Optional[str] = Query(None, min_length=1)
):
    """Get order details by part number or part description"""
    try:
        with db_session:
            if part_number and part_description:
                raise HTTPException(
                    status_code=400,
                    detail="Please provide either a part number or part description, but not both."
                )

            if part_number:
                orders = select(o for o in Order if part_number.lower() in o.part_number.lower())[:]
            elif part_description:
                orders = select(o for o in Order if part_description.lower() in o.part_description.lower())[:]
            else:
                orders = []

            if not orders:
                return {"orders": []}

            response_data = {
                "orders": [
                    {
                        "id": order.id,
                        "production_order": order.production_order,
                        "sale_order": order.sale_order,
                        "wbs_element": order.wbs_element,
                        "part_number": order.part_number,
                        "part_description": order.part_description,
                        "total_operations": order.total_operations,
                        "required_quantity": order.required_quantity,
                        "launched_quantity": order.launched_quantity,
                        "plant_id": order.plant_id,
                        "project": {
                            "id": order.project.id,
                            "name": order.project.name,
                            "priority": order.project.priority,
                            "start_date": order.project.start_date,
                            "end_date": order.project.end_date
                        } if order.project else None,
                        "operations": [
                            {
                                "id": op.id,
                                "operation_number": op.operation_number,
                                "operation_description": op.operation_description,
                                "setup_time": op.setup_time,
                                "ideal_cycle_time": op.ideal_cycle_time,
                                "work_center": op.work_center.code if op.work_center else None
                            }
                            for op in order.operations
                        ]
                    }
                    for order in orders
                ]
            }

            return response_data

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")


@router.put("/update_order/{order_number}")
async def update_order(order_number: str, update_data: OrderUpdateRequest):
    try:
        # Convert plant_id to string if it's an integer
        if isinstance(update_data.plant_id, int):
            update_data.plant_id = str(update_data.plant_id)

        with db_session:
            # Fetch the order by its production_order field
            order = Order.get(production_order=order_number)
            if not order:
                raise HTTPException(
                    status_code=404,
                    detail=f"Order with number {order_number} not found"
                )

            # Convert the update_data into a dictionary while excluding unset fields
            update_dict = update_data.dict(exclude_unset=True)

            # Validate and convert required_quantity to int
            if 'required_quantity' in update_dict:
                try:
                    update_dict['required_quantity'] = int(update_dict['required_quantity'])
                except (ValueError, TypeError):
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid value for required_quantity. Must be an integer."
                    )

            # Handle delivery_date separately (if provided as epoch)
            if 'delivery_date' in update_dict:
                epoch_timestamp = update_dict.pop('delivery_date')
                if epoch_timestamp is not None:
                    try:
                        delivery_date = datetime.fromtimestamp(epoch_timestamp)
                        order.delivery_date = delivery_date
                        # Update project end date if needed
                        if order.project and delivery_date > order.project.end_date:
                            order.project.end_date = delivery_date
                    except ValueError as e:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invalid delivery date timestamp: {str(e)}"
                        )

            # Update all remaining fields
            for field, value in update_dict.items():
                if hasattr(order, field):
                    setattr(order, field, value)

            # Commit the changes
            commit()

            # Return the updated order as a dictionary
            return order.to_dict()

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/operations/{part_number}/{operation_number}")
async def update_operation(
        part_number: str,
        operation_number: int,
        operation_data: OperationUpdateRequest,
        production_order: Optional[str] = Query(None)  # Get from query parameter instead
):
    try:
        with db_session:
            # Find the order using both part_number and production_order
            if production_order:
                order = Order.get(part_number=part_number, production_order=production_order)
            else:
                # Fallback to just part_number, but this may be ambiguous
                order = Order.get(part_number=part_number)

            if not order:
                raise HTTPException(
                    status_code=404,
                    detail=f"No order found with part number {part_number} and production order {production_order}"
                )

            operation = select(op for op in Operation
                               if op.order == order and op.operation_number == operation_number).first()
            if not operation:
                raise HTTPException(
                    status_code=404,
                    detail=f"Operation {operation_number} not found"
                )

            # Update operation fields from the validated request model
            update_dict = operation_data.dict(exclude_unset=True)

            if 'operation_description' in update_dict:
                operation.operation_description = update_dict['operation_description']
            if 'setup_time' in update_dict:
                operation.setup_time = update_dict['setup_time']
            if 'ideal_cycle_time' in update_dict:
                operation.ideal_cycle_time = update_dict['ideal_cycle_time']
            if 'work_center_code' in update_dict:
                work_center = WorkCenter.get(code=update_dict['work_center_code'])
                if not work_center:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Work center {update_dict['work_center_code']} not found"
                    )
                operation.work_center = work_center

            # Add machine ID update
            if 'machine_id' in update_dict:
                machine = Machine.get(id=update_dict['machine_id'])
                if not machine:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Machine with ID {update_dict['machine_id']} not found"
                    )
                # Ensure the machine belongs to the same work center
                if machine.work_center != operation.work_center:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Machine {update_dict['machine_id']} does not belong to work center {operation.work_center.code}"
                    )
                operation.machine = machine

            commit()
            return operation.to_dict()

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/create_order1")
async def create_order(order_data: CreateOrderRequest_new):
    """Create a new order"""
    try:
        with db_session:
            print(f"DEBUG: Starting order creation for production_order: {order_data.production_order}")

            # Check if order already exists
            existing_order = Order.get(production_order=order_data.production_order)
            if existing_order:
                print(f"DEBUG: Order already exists with production_order: {order_data.production_order}")
                raise HTTPException(
                    status_code=400,
                    detail="Production order already exists"
                )

            # Current date for project dates
            current_date = datetime.now()
            print(f"DEBUG: Current date: {current_date}")

            # Get or create project - Fixed to handle multiple projects
            print(f"DEBUG: Looking for project with name: {order_data.project_name}")

            # Use select to get all projects with this name
            # existing_projects = list(Project.select(lambda p: p.name == order_data.project_name))
            # print(f"DEBUG: Found {len(existing_projects)} existing projects with name '{order_data.project_name}'")
            #
            # if existing_projects:
            #     # Use the first existing project
            #     project = existing_projects[0]
            #     print(f"DEBUG: Using existing project with ID: {project.id}")
            # else:
            #     # Create new project
            #     print("DEBUG: Creating new project")
            #     max_priority = select(max(p.priority) for p in Project).first() or 0
            #     print(f"DEBUG: Max priority found: {max_priority}")
            #
            #     project = Project(
            #         name=order_data.project_name,
            #         priority=max_priority + 1,  # Auto-increment
            #         start_date=current_date,
            #         end_date=current_date,
            #         delivery_date=current_date
            #     )
            #     print(f"DEBUG: Created new project with priority: {project.priority}")

            print("DEBUG: Creating new project")
            max_priority = select(max(p.priority) for p in Project).first() or 0
            print(f"DEBUG: Max priority found: {max_priority}")

            project = Project(
                name=order_data.project_name,
                priority=max_priority + 1,  # Auto-increment
                start_date=current_date,
                end_date=current_date,
                delivery_date=current_date
            )

            print(f"DEBUG: Created new project with priority: {project.priority}")

            # Get or create unit based on user input
            print(f"DEBUG: Looking for unit with name: {order_data.raw_material_unit_name}")

            # Use select to handle potential multiple units
            existing_units = list(Unit.select(lambda u: u.name == order_data.raw_material_unit_name))
            print(f"DEBUG: Found {len(existing_units)} existing units with name '{order_data.raw_material_unit_name}'")

            if existing_units:
                raw_material_unit = existing_units[0]
                print(f"DEBUG: Using existing unit with ID: {raw_material_unit.id}")
            else:
                raw_material_unit = Unit(name=order_data.raw_material_unit_name)
                print(f"DEBUG: Created new unit: {order_data.raw_material_unit_name}")

            # Check if raw material already exists with the same part number
            print(f"DEBUG: Checking for existing raw material with part number: {order_data.raw_material_part_number}")

            # Use select to handle potential multiple raw materials
            existing_raw_materials = list(
                RawMaterial.select(lambda rm: rm.child_part_number == order_data.raw_material_part_number))
            print(
                f"DEBUG: Found {len(existing_raw_materials)} existing raw materials with part number '{order_data.raw_material_part_number}'")

            if existing_raw_materials:
                print(f"DEBUG: Raw material already exists with part number: {order_data.raw_material_part_number}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Raw material with part number '{order_data.raw_material_part_number}' already exists"
                )

            # Get or create default inventory status
            print("DEBUG: Looking for default inventory status 'Available'")

            # Use select to handle potential multiple statuses
            existing_statuses = list(InventoryStatus.select(lambda s: s.name == "Available"))
            print(f"DEBUG: Found {len(existing_statuses)} existing statuses with name 'Available'")

            if existing_statuses:
                default_status = existing_statuses[0]
                print(f"DEBUG: Using existing status with ID: {default_status.id}")
            else:
                default_status = InventoryStatus(
                    name="Available",
                    description="Material is available for use"
                )
                print("DEBUG: Created new 'Available' status")

            # Create new raw material with user-provided data
            print("DEBUG: Creating new raw material")
            raw_material = RawMaterial(
                child_part_number=order_data.raw_material_part_number,
                description=order_data.raw_material_description,
                quantity=order_data.raw_material_quantity,
                unit=raw_material_unit,
                status=default_status,
                available_from=datetime(2024, 1, 2, 9, 0)  # Hardcoded available_from date
            )
            print(f"DEBUG: Created raw material with part number: {raw_material.child_part_number}")

            # Create new order
            print("DEBUG: Creating new order")
            order = Order(
                production_order=order_data.production_order,
                sale_order=order_data.sale_order,
                wbs_element=order_data.wbs_element,
                part_number=order_data.part_number,
                part_description=order_data.part_description,
                total_operations=order_data.total_operations,
                required_quantity=order_data.required_quantity,
                launched_quantity=order_data.launched_quantity,
                plant_id=str(order_data.plant_id),  # Convert to string as required by model
                project=project,
                raw_material=raw_material  # Link the raw material to the order
            )
            print(f"DEBUG: Created order with ID: {order.id}")

            # Create initial 'inactive' status for scheduling
            print("DEBUG: Creating part schedule status")

            # Use select to check for existing part status
            existing_part_statuses = list(PartScheduleStatus.select(
                lambda
                    ps: ps.part_number == order_data.part_number and ps.production_order == order_data.production_order
            ))
            print(f"DEBUG: Found {len(existing_part_statuses)} existing part statuses")

            if not existing_part_statuses:
                part_status = PartScheduleStatus(
                    part_number=order_data.part_number,
                    production_order=order_data.production_order,
                    status='inactive'  # Default to inactive when order is created
                )
                print("DEBUG: Created new part schedule status")
            else:
                print("DEBUG: Part schedule status already exists")

            # Check if there are existing operations for this part number that we should duplicate
            print(f"DEBUG: Looking for similar orders with part number: {order_data.part_number}")

            # First, find other orders with the same part number
            similar_orders = list(
                select(o for o in Order if o.part_number == order_data.part_number and o.id != order.id))
            print(f"DEBUG: Found {len(similar_orders)} similar orders")

            # If there are similar orders, duplicate their operations
            if similar_orders:
                # Get the first similar order
                source_order = similar_orders[0]
                print(f"DEBUG: Using source order ID: {source_order.id} for operation duplication")

                # Get all operations from the source order
                source_operations = list(select(op for op in Operation if op.order == source_order))
                print(f"DEBUG: Found {len(source_operations)} operations to duplicate")

                # Duplicate each operation for the new order
                for i, source_op in enumerate(source_operations):
                    new_operation = Operation(
                        order=order,
                        operation_number=source_op.operation_number,
                        operation_description=source_op.operation_description,
                        setup_time=source_op.setup_time,
                        ideal_cycle_time=source_op.ideal_cycle_time,
                        work_center=source_op.work_center,
                        machine=source_op.machine
                    )
                    print(f"DEBUG: Duplicated operation {i + 1}/{len(source_operations)}: {source_op.operation_number}")

                # Update total operations count
                order.total_operations = len(source_operations)
                print(f"DEBUG: Updated total operations to: {order.total_operations}")
            else:
                print("DEBUG: No similar orders found, no operations to duplicate")

            print("DEBUG: Committing transaction")
            commit()

            print("DEBUG: Order creation completed successfully")
            return {
                "id": order.id,
                "production_order": order.production_order,
                "sale_order": order.sale_order,
                "wbs_element": order.wbs_element,
                "part_number": order.part_number,
                "part_description": order.part_description,
                "total_operations": order.total_operations,
                "required_quantity": order.required_quantity,
                "launched_quantity": order.launched_quantity,
                "plant_id": order.plant_id,
                "project": {
                    "id": order.project.id,
                    "name": order.project.name,
                    "priority": order.project.priority,
                    "start_date": order.project.start_date,
                    "end_date": order.project.end_date,
                    "delivery_date": order.project.delivery_date
                },
                "raw_material": {
                    "id": order.raw_material.id,
                    "child_part_number": order.raw_material.child_part_number,
                    "description": order.raw_material.description,
                    "quantity": order.raw_material.quantity,
                    "available_from": order.raw_material.available_from,
                    "unit": {
                        "id": order.raw_material.unit.id,
                        "name": order.raw_material.unit.name
                    },
                    "status": {
                        "id": order.raw_material.status.id,
                        "name": order.raw_material.status.name
                    }
                }
            }

    except HTTPException as he:
        print(f"DEBUG: HTTPException raised: {he.detail}")
        raise he
    except Exception as e:
        print(f"DEBUG: Unexpected error: {str(e)}")
        print(f"DEBUG: Error type: {type(e).__name__}")
        import traceback
        print(f"DEBUG: Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Error creating order: {str(e)}"
        )


@router.post("/create_order")
async def create_order(order_data: CreateOrderRequest):
    """Create a new order"""
    try:
        with db_session:
            # Check if order already exists
            existing_order = Order.get(production_order=order_data.production_order)
            if existing_order:
                raise HTTPException(
                    status_code=400,
                    detail="Production order already exists"
                )

            # Current date for project dates
            current_date = datetime.now()

            # Get or create project
            project = Project.get(name=order_data.project_name)
            if not project:
                max_priority = select(max(p.priority) for p in Project).first() or 0
                project = Project(
                    name=order_data.project_name,
                    priority=max_priority + 1,  # Auto-increment
                    start_date=current_date,
                    end_date=current_date,
                    delivery_date=current_date
                )

            # Get or create default inventory status
            default_status = InventoryStatus.get(name="Available")
            if not default_status:
                default_status = InventoryStatus(
                    name="Available",
                    description="Material is available for use"
                )

            # Create raw material with hardcoded available_from date
            raw_material = RawMaterial(
                child_part_number=f"RM-{order_data.part_number}",  # Generate a default part number
                description=f"Raw Material for {order_data.part_number}",
                quantity=float(order_data.required_quantity),  # Use required quantity as default
                unit=Unit.get(name="KG") or Unit(name="KG"),  # Get or create PCS unit
                status=default_status,
                available_from=datetime(2024, 1, 2, 9, 0)  # Hardcoded available_from date
            )

            # Create new order
            order = Order(
                production_order=order_data.production_order,
                sale_order=order_data.sale_order,
                wbs_element=order_data.wbs_element,
                part_number=order_data.part_number,
                part_description=order_data.part_description,
                total_operations=order_data.total_operations,
                required_quantity=order_data.required_quantity,
                launched_quantity=order_data.launched_quantity,
                plant_id=str(order_data.plant_id),  # Convert to string as required by model
                project=project,
                raw_material=raw_material  # Link the raw material to the order
            )

            # Create initial 'inactive' status for scheduling
            # FIX: Updated to use both part_number and production_order
            part_status = PartScheduleStatus.get(
                part_number=order_data.part_number,
                production_order=order_data.production_order
            )
            if not part_status:
                PartScheduleStatus(
                    part_number=order_data.part_number,
                    production_order=order_data.production_order,
                    status='inactive'  # Default to inactive when order is created
                )

            # Check if there are existing operations for this part number that we should duplicate
            # First, find other orders with the same part number
            similar_orders = select(o for o in Order if o.part_number == order_data.part_number and o.id != order.id)[:]

            # If there are similar orders, duplicate their operations
            if similar_orders:
                # Get the first similar order
                source_order = similar_orders[0]

                # Get all operations from the source order
                source_operations = select(op for op in Operation if op.order == source_order)[:]

                # Duplicate each operation for the new order
                for source_op in source_operations:
                    Operation(
                        order=order,
                        operation_number=source_op.operation_number,
                        operation_description=source_op.operation_description,
                        setup_time=source_op.setup_time,
                        ideal_cycle_time=source_op.ideal_cycle_time,
                        work_center=source_op.work_center,
                        machine=source_op.machine
                    )

                # Update total operations count
                order.total_operations = len(source_operations)

            commit()
            return {
                "id": order.id,
                "production_order": order.production_order,
                "sale_order": order.sale_order,
                "wbs_element": order.wbs_element,
                "part_number": order.part_number,
                "part_description": order.part_description,
                "total_operations": order.total_operations,
                "required_quantity": order.required_quantity,
                "launched_quantity": order.launched_quantity,
                "plant_id": order.plant_id,
                "project": {
                    "id": order.project.id,
                    "name": order.project.name,
                    "priority": order.project.priority,
                    "start_date": order.project.start_date,
                    "end_date": order.project.end_date,
                    "delivery_date": order.project.delivery_date
                },
                "raw_material": {
                    "id": order.raw_material.id,
                    "child_part_number": order.raw_material.child_part_number,
                    "description": order.raw_material.description,
                    "quantity": order.raw_material.quantity,
                    "available_from": order.raw_material.available_from,
                    "unit": {
                        "id": order.raw_material.unit.id,
                        "name": order.raw_material.unit.name
                    },
                    "status": {
                        "id": order.raw_material.status.id,
                        "name": order.raw_material.status.name
                    }
                }
            }

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error creating order: {str(e)}"
        )



@router.post("/operations")
async def create_operation(operation_data: CreateOperationRequest):
    """Create a new operation for an existing order"""
    try:
        with db_session:
            # Find the order
            order = Order.get(id=operation_data.order_id)
            if not order:
                raise HTTPException(
                    status_code=404,
                    detail="Order not found"
                )

            # Find the work center
            work_center = WorkCenter.get(code=operation_data.work_center_code)
            if not work_center:
                raise HTTPException(
                    status_code=404,
                    detail=f"Work center {operation_data.work_center_code} not found"
                )

            # Check if operation number already exists
            existing_op = select(op for op in Operation
                               if op.order == order and
                               op.operation_number == operation_data.operation_number).first()
            if existing_op:
                raise HTTPException(
                    status_code=400,
                    detail=f"Operation number {operation_data.operation_number} already exists"
                )

            # Get existing machine or create a default one
            machine = select(m for m in Machine if m.work_center == work_center).first()
            if not machine:
                # Create a default machine for the work center
                machine = Machine(
                    work_center=work_center,
                    type="Default",
                    make="Default",
                    model="Default"
                )

            # Create new operation
            operation = Operation(
                order=order,
                operation_number=operation_data.operation_number,
                work_center=work_center,
                machine=machine,  # Add the machine assignment
                operation_description=operation_data.operation_description,
                setup_time=operation_data.setup_time,
                ideal_cycle_time=operation_data.ideal_cycle_time
            )

            # Update order's total operations
            order.total_operations = count(op for op in Operation if op.order == order)

            commit()
            return operation.to_dict()

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error creating operation: {str(e)}"
        )

@router.get("/work_centers")
async def get_work_centers():
    """Get all work centers"""
    try:
        with db_session:
            work_centers = select(w for w in WorkCenter)[:]
            return [{"id": wc.id, "code": wc.code} for wc in work_centers]
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving work centers: {str(e)}"
        )


@router.get("/search_order2")
async def search_order(
        production_order: Optional[str] = Query(None, min_length=1)
):
    """Get order details by production order number"""
    try:
        with db_session:
            if not production_order:
                return {"orders": []}

            # Exact match search - change from substring match to exact match
            orders = select(o for o in Order if o.production_order == production_order)[:]

            if not orders:
                # If no exact match found, fall back to partial match as a secondary option
                orders = select(o for o in Order if production_order.lower() in o.production_order.lower())[:]

            if not orders:
                return {"orders": []}

            response_data = {
                "orders": []
            }

            for order in orders:
                # Get raw materials associated with this order
                raw_materials = select(rm for rm in RawMaterial if order in rm.orders)[:]

                order_data = {
                    "id": order.id,
                    "production_order": order.production_order,
                    "sale_order": order.sale_order,
                    "wbs_element": order.wbs_element,
                    "part_number": order.part_number,
                    "part_description": order.part_description,
                    "total_operations": order.total_operations,
                    "required_quantity": order.required_quantity,
                    "launched_quantity": order.launched_quantity,
                    "plant_id": order.plant_id,
                    "project": {
                        "id": order.project.id,
                        "name": order.project.name,
                        "priority": order.project.priority,
                        "start_date": order.project.start_date,
                        "end_date": order.project.end_date
                    } if order.project else None,
                    "raw_materials": [
                        {
                            "id": raw_material.id,
                            "child_part_number": raw_material.child_part_number,
                            "description": raw_material.description,
                            "quantity": float(raw_material.quantity),
                            "unit": {
                                "id": raw_material.unit.id,
                                "name": raw_material.unit.name
                            },
                            "status": {
                                "id": raw_material.status.id,
                                "name": raw_material.status.name
                            },
                            "available_from": raw_material.available_from.isoformat() if raw_material.available_from else None
                        }
                        for raw_material in raw_materials
                    ],
                    "operations": [
                        {
                            "id": op.id,
                            "operation_number": op.operation_number,
                            "operation_description": op.operation_description,
                            "setup_time": op.setup_time,
                            "ideal_cycle_time": op.ideal_cycle_time,
                            "work_center": op.work_center.code if op.work_center else None,
                            "boolean": op.work_center.is_schedulable,
                            "primary_machine": {
                                "id": op.machine.id,
                                "name": f"{op.machine.make} {op.machine.model}"
                            } if op.machine else None,
                            "work_center_machines": [
                                {
                                    "id": machine.id,
                                    "make": machine.make,
                                    "model": machine.model,
                                    "type": machine.type
                                }
                                for machine in op.work_center.machines
                            ] if op.work_center else []
                        }
                        for op in order.operations
                    ]
                }

                response_data["orders"].append(order_data)

            return response_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")


@router.post("/upload-pdf")
async def upload_pdf(file: UploadFile = File(...)):
    try:
        pdf_content = await file.read()
        data = extract_oarc_details(io.BytesIO(pdf_content))

        # Convert any non-serializable objects to strings
        json_compatible_data = json.loads(json.dumps(data, default=str))
        return json_compatible_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/save-to-db")
def save_to_database_endpoint(request: SaveDataRequest):
    try:
        with db_session:
            master_order = save_to_database(request.data)

            response_data = {
                "message": "Data saved successfully",
                "order_details": {
                    "id": master_order.id,
                    "production_order": master_order.production_order,
                    "sale_order": master_order.sale_order,
                    "wbs_element": master_order.wbs_element,
                    "part_number": master_order.part_number,
                    "part_description": master_order.part_description,
                    "total_operations": master_order.total_operations,
                    "required_quantity": master_order.required_quantity,
                    "launched_quantity": master_order.launched_quantity,
                    "plant_id": master_order.plant_id,
                    "project": {
                        "id": master_order.project.id,
                        "name": master_order.project.name,
                        "priority": master_order.project.priority,
                        "delivery_date": master_order.project.delivery_date,
                        "start_date": master_order.project.start_date,
                        "end_date": master_order.project.end_date
                    } if master_order.project else None,
                    "raw_material": {
                        "id": master_order.raw_material.id,
                        "child_part_number": master_order.raw_material.child_part_number,
                        "description": master_order.raw_material.description,
                        "quantity": master_order.raw_material.quantity,
                        "unit": {
                            "id": master_order.raw_material.unit.id,
                            "name": master_order.raw_material.unit.name
                        } if master_order.raw_material.unit else None,
                        "status": {
                            "id": master_order.raw_material.status.id,
                            "name": master_order.raw_material.status.name
                        } if master_order.raw_material.status else None
                    } if master_order.raw_material else None
                }
            }
            return response_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/order/{order_id}/priority")
async def update_order_priority(order_id: int, priority_data: ProjectPriorityUpdateRequest):
    """
    Update the priority of a project associated with an order and reorder other projects.

    If any project is moved to a higher priority (lower number), all projects between the
    new priority and the old priority will be shifted down (increment priority number).

    If any project is moved to a lower priority (higher number), all projects between the
    old priority and the new priority will be shifted up (decrement priority number).
    """
    try:
        with db_session:
            # Find the order by ID
            order = Order.get(id=order_id)
            if not order:
                raise HTTPException(
                    status_code=404,
                    detail=f"Order with ID {order_id} not found"
                )

            # Check if order has an associated project
            if not order.project:
                raise HTTPException(
                    status_code=404,
                    detail=f"Order with ID {order_id} does not have an associated project"
                )

            current_project = order.project
            old_priority = current_project.priority
            new_priority = priority_data.priority

            # If the priority is the same, no change needed
            if old_priority == new_priority:
                return {
                    "message": "No change in priority",
                    "project_id": current_project.id,
                    "priority": current_project.priority
                }

            # Get all projects ordered by priority
            all_projects = select(p for p in Project).order_by(Project.priority)[:]

            # Moving to a higher priority (lower number)
            if new_priority < old_priority:
                # Shift down projects that are between new and old priority (inclusive of new, exclusive of old)
                for project in all_projects:
                    if project.id != current_project.id:
                        if new_priority <= project.priority < old_priority:
                            project.priority += 1

            # Moving to a lower priority (higher number)
            elif new_priority > old_priority:
                # Shift up projects that are between old and new priority (exclusive of old, inclusive of new)
                for project in all_projects:
                    if project.id != current_project.id:
                        if old_priority < project.priority <= new_priority:
                            project.priority -= 1

            # Set the new priority for the current project
            current_project.priority = new_priority

            # Commit the changes
            commit()

            # Get updated projects to verify changes
            updated_projects = select(p for p in Project).order_by(Project.priority)[:]

            # Return a success response
            return {
                "message": "Project priority updated successfully",
                "project_id": current_project.id,
                "new_priority": current_project.priority,
                "updated_priorities": [
                    {"project_id": p.id, "name": p.name, "priority": p.priority}
                    for p in updated_projects
                ]
            }

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating project priority: {str(e)}"
        )


@router.get("/projects/priority")
async def get_project_priorities():
    """
    Get all projects with their priorities and associated order details.

    Returns a list of projects ordered by priority (ascending) with production order details
    including Production Order, Part Number, Material Description, Quantity Status,
    WBS Element, and Sales Order.
    """
    try:
        with db_session:
            # Get all projects ordered by priority (ascending)
            projects = select(p for p in Project).order_by(Project.priority)[:]

            response_data = []

            for project in projects:
                # Get all orders associated with this project
                orders = select(o for o in Order if o.project == project)[:]

                project_orders = []
                for order in orders:
                    # Get status from PartScheduleStatus if it exists
                    status = select(
                        ps.status for ps in PartScheduleStatus if ps.part_number == order.part_number).first()
                    status = status if status else "unknown"

                    project_orders.append({
                        "production_order": order.production_order,
                        "part_number": order.part_number,
                        "material_description": order.part_description,
                        "quantity": order.required_quantity,
                        "status": status,
                        "wbs_element": order.wbs_element,
                        "sales_order": order.sale_order
                    })

                response_data.append({
                    "project_id": project.id,
                    "project_name": project.name,
                    "priority": project.priority,

                    "orders": project_orders
                })

            return {"projects": response_data}

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving project priorities: {str(e)}"
        )


@router.delete("/orders/{order_id}", status_code=200)
@db_session
def delete_order(order_id: int):
    order = Order.get(id=order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Save references to foreign keys only after confirming order exists
    raw_material_ref = order.raw_material
    project_ref = order.project

    # Find and delete associated PartScheduleStatus if it exists
    production_order = order.production_order if hasattr(order, 'production_order') else str(order_id)
    part_schedule_status = PartScheduleStatus.get(production_order=production_order)
    if part_schedule_status:
        part_schedule_status.delete()

    # Delete all related child entities
    for op in order.operations:
        op.delete()
    for tool in order.tools:
        tool.delete()
    for jig in order.jigs_fixtures:
        jig.delete()
    for mpp in order.mpps:
        mpp.delete()
    for psi in order.planned_schedule_items:
        psi.delete()
    for req in order.inventory_requests:
        req.delete()
    for ot in order.order_tools:
        ot.delete()
    for req in order.inventory_requests:
        req.delete()

    # Handle DocumentV2 entities - preserve IPID documents
    for docv2 in order.documents_v2:
        if docv2.doc_type.name == "IPID":
            docv2.production_order = None
        elif docv2.doc_type.name == "REPORT":
            docv2.production_order = None
        else:
            docv2.delete()

    # Delete the order itself
    order.delete()

    # Clean up orphaned references if they're not used by other orders
    # --- PRIORITY REARRANGEMENT LOGIC STARTS HERE ---
    if project_ref:
        # Check if project has no more orders after deletion
        if not project_ref.orders:
            deleted_priority = project_ref.priority
            project_ref.delete()
            # Rearrange priorities for remaining projects
            for proj in Project.select(lambda p: p.priority > deleted_priority):
                proj.priority -= 1
    # --- PRIORITY REARRANGEMENT LOGIC ENDS HERE ---

    if raw_material_ref and not raw_material_ref.orders:
        raw_material_ref.delete()

    commit()
    return {"message": "Order and related entities deleted successfully."}


@router.put("/orders/{order_id}", response_model=OrderUpdate_Response)
@db_session
def update_order(order_id: int, order_update: OrderUpdate_Request):
    """
    Update editable fields of an existing order.

    Editable fields:
    - part_description
    - wbs_element
    - launched_quantity
    - project_name (updates the associated project)
    - sale_order
    """
    try:
        # Get the order by ID
        order = get(o for o in Order if o.id == order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        # Track if any changes were made
        changes_made = False

        # Update part_description if provided
        if order_update.part_description is not None:
            order.part_description = order_update.part_description
            changes_made = True

        # Update wbs_element if provided
        if order_update.wbs_element is not None:
            order.wbs_element = order_update.wbs_element
            changes_made = True

        # Update launched_quantity if provided
        if order_update.launched_quantity is not None:
            if order_update.launched_quantity < 0:
                raise HTTPException(
                    status_code=400,
                    detail="Launched quantity cannot be negative"
                )
            order.launched_quantity = order_update.launched_quantity
            changes_made = True

        # Update sale_order if provided
        if order_update.sale_order is not None:
            order.sale_order = order_update.sale_order
            changes_made = True

        # Update project_name if provided
        if order_update.project_name is not None:
            # Update the name of the existing project associated with this order
            order.project.name = order_update.project_name
            changes_made = True

        if not changes_made:
            raise HTTPException(
                status_code=400,
                detail="No valid fields provided for update"
            )

        # Commit changes (handled by @db_session decorator)

        # Return updated order data
        return OrderUpdate_Response(
            id=order.id,
            production_order=order.production_order,
            part_description=order.part_description,
            wbs_element=order.wbs_element,
            launched_quantity=order.launched_quantity,
            project_name=order.project.name,
            sale_order=order.sale_order,
            updated_at=datetime.now()
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")