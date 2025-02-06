from fastapi import FastAPI, File, UploadFile, APIRouter, HTTPException, Query
from pony.orm import db_session, select, commit, count
from datetime import datetime
from typing import List, Optional
import PyPDF2
import io
import re

from app.database.connection import db
from app.models import (
    WorkCenter, Machine, Project, Order, Operation,
    ProcessPlan, Document, ToolList, JigsAndFixturesList,
    Unit, RawMaterial, InventoryStatus, PartScheduleStatus
)
from app.schemas.planning import CreateOperationRequest, CreateOrderRequest, OrderUpdateRequest, OperationUpdateRequest

router = APIRouter(prefix="/planning", tags=["planning"])


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
    plant_match = re.search(r"Plant\s*:([^R]+)Rtg Seq No\s*:([^S]+)Sequence No\s*:([^\n]+)", text)
    if plant_match:
        data["Plant"] = plant_match.group(1).strip()
        data["Rtg Seq No"] = plant_match.group(2).strip()
        data["Sequence No"] = plant_match.group(3).strip()

    # Required Qty, Launched Qty, and Prod Order No
    qty_match = re.search(r"Required Qty\s*:([^L]+)Launched Qty\s*:([^P]+)Prod Order No\s*:([^\n]+)", text)
    if qty_match:
        data["Required Qty"] = qty_match.group(1).strip()
        data["Launched Qty"] = qty_match.group(2).strip()
        data["Prod Order No"] = qty_match.group(3).strip()

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

    # Extract document verification details from long text when operation is verification
    # for operation in data["Operations"]:
    #     if "verification" in operation["Operation"].lower():
    #         doc_details = {}
    #         long_text = operation["Long Text"]
    #
    #         # Extract document details using regex patterns
    #         doc_patterns = {
    #             "OARC Rev": r"OARC Rev\.\s*:\s*([^\n]+)",
    #             "Part Rev": r"Part Rev\.\s*:\s*([^\n]+)",
    #             "Drawing No": r"Drawing No\.\s*:\s*([^R]+)Rev\.\s*:\s*([^\n]+)",
    #             "Cad No": r"Cad No\.\s*:\s*([^R]+)Rev\.\s*:\s*([^\n]+)",
    #             "Stage Verification Doc": r"Stage Verification Document No\.\s*:\s*([^R]+)Rev\.\s*:\s*([^\n]+)",
    #             "Final Verification Doc": r"Final Verification Document No\.\s*:\s*([^R]+)Rev\.\s*:\s*([^\n]+)",
    #             "Raw Material Index Doc": r"Raw Material Index\s+Doc No\.\s*:\s*([\w\-]+)\s+Rev\.\s*:\s*(\d+)",
    #             "Plating Inspection Doc": r"Plating inspection Doc No\.\s*:([\w\-]+)\s+Rev\.\s*:\s*(\d+)",
    #             "MPP Doc": r"MPP Doc No\.\s*:\s*([^R]+)Rev\.\s*:\s*([^\n]+)"
    #         }
    #
    #         for key, pattern in doc_patterns.items():
    #             match = re.search(pattern, long_text)
    #             if match:
    #                 if len(match.groups()) == 2:
    #                     doc_details[key] = {
    #                         "Number": match.group(1).strip(),
    #                         "Revision": match.group(2).strip()
    #                     }
    #                 else:
    #                     doc_details[key] = match.group(1).strip()
    #
    #         data["Document Verification"] = doc_details
    #         break

    # Extract raw materials
    raw_materials_started = False
    raw_material_pattern = r'(\d{4})\s+(\w+)\s+([\w\s\-\.]+)\s+([\d\.]+)\s+(\w+)\s+([\d\.]+)'

    for i, line in enumerate(lines):
        line = line.strip()

        # Check if we've reached the raw materials section
        if "Item" in line and "Child Part No" in line:
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

    return data


@router.post("/upload-pdf")
async def upload_pdf(file: UploadFile = File(...)):
    try:
        pdf_content = await file.read()
        data = extract_oarc_details(io.BytesIO(pdf_content))

        with db_session:
            master_order = save_to_database(data)

            # Prepare detailed response
            response_data = {
                "message": "PDF uploaded and data saved successfully",
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


@db_session
def save_to_database(data):
    try:
        # Check if order exists
        existing_order = Order.get(production_order=data["Prod Order No"])
        if existing_order:
            return existing_order

        # Get or create project
        project = Project.get(name=data["Project Name"])
        if not project:
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

        # Create raw material
        unit = Unit.get(name=data["Raw Materials"][0]["UoM"])
        if not unit:
            unit = Unit(name=data["Raw Materials"][0]["UoM"])

        raw_material = RawMaterial(
            child_part_number=data["Raw Materials"][0]["Child Part No"],
            description=data["Raw Materials"][0]["Description"],
            quantity=float(data["Raw Materials"][0]["Total Qty"]),
            unit=unit,
            status=default_status
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

        # Create initial 'inactive' status for scheduling
        part_status = PartScheduleStatus.get(part_number=data["Part No"])
        if not part_status:
            PartScheduleStatus(
                part_number=data["Part No"],
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
                machine = Machine(
                    work_center=work_center,
                    type="Default",
                    make="Default",
                    model="Default"
                )
            else:
                # Use the first existing machine
                machine = existing_machines[0]

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


@router.get("/all_orders")
@db_session
def get_all_orders():
    try:
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
                                "work_center": op.work_center.code if op.work_center else None,
                                "machine": {
                                    "id": op.machine.id,
                                    "name": f"{op.machine.make} {op.machine.model}"
                                } if op.machine else None
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
        operation_data: OperationUpdateRequest
):
    try:
        with db_session:
            order = Order.get(part_number=part_number)
            if not order:
                raise HTTPException(
                    status_code=404,
                    detail=f"No order found with part number {part_number}"
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

            # Convert epoch to datetime for delivery_date
            try:
                delivery_date = datetime.fromtimestamp(order_data.delivery_date)
            except ValueError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid delivery date timestamp: {str(e)}"
                )

            # Get or create project
            project = Project.get(name=order_data.project_name)
            if not project:
                max_priority = select(max(p.priority) for p in Project).first() or 0
                project = Project(
                    name=order_data.project_name,
                    priority=max_priority + 1,  # Auto-increment
                    start_date=datetime.now(),
                    end_date=delivery_date,
                    delivery_date=delivery_date
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
                raw_material="",  # Default empty string
                plant_id=order_data.plant_id,
                delivery_date=delivery_date,
                project=project
            )

            # Create initial 'inactive' status for scheduling
            part_status = PartScheduleStatus.get(part_number=order_data.part_number)
            if not part_status:
                PartScheduleStatus(
                    part_number=order_data.part_number,
                    status='inactive'  # Default to inactive when order is created
                )

            commit()
            return order.to_dict()

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