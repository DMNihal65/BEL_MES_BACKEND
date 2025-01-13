from fastapi import FastAPI, File, UploadFile, APIRouter, HTTPException, Query, Depends
from fastapi import FastAPI, File, UploadFile, APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from Database.db_setup import engine, Base, SessionLocal
from Database.db_setup import engine, Base, get_db
from orm_class.master_order import *
from orm_class.inventory import *
from orm_class.planning_scheduling import *
import PyPDF2
import re
import io
from Crud.planning import getAllOrders, update_order, create_order
from pydantic_schema.respose_models import GetAllOrders, WorkCenterResponse
from typing import List
from pydantic_schema.request_body import OrderUpdate, OrderCreate
from Crud.planning import getAllOrders, update_order
from pydantic_schema.respose_models import GetAllOrders, OperationResponse
from typing import List, Optional
from pydantic_schema.request_body import OrderUpdate, ResponseModel, OperationUpdate, OperationCreate

router = APIRouter()


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
    for operation in data["Operations"]:
        if "verification" in operation["Operation"].lower():
            doc_details = {}
            long_text = operation["Long Text"]

            # Extract document details using regex patterns
            doc_patterns = {
                "OARC Rev": r"OARC Rev\.\s*:\s*([^\n]+)",
                "Part Rev": r"Part Rev\.\s*:\s*([^\n]+)",
                "Drawing No": r"Drawing No\.\s*:\s*([^R]+)Rev\.\s*:\s*([^\n]+)",
                "Cad No": r"Cad No\.\s*:\s*([^R]+)Rev\.\s*:\s*([^\n]+)",
                "Stage Verification Doc": r"Stage Verification Document No\.\s*:\s*([^R]+)Rev\.\s*:\s*([^\n]+)",
                "Final Verification Doc": r"Final Verification Document No\.\s*:\s*([^R]+)Rev\.\s*:\s*([^\n]+)",
                "Raw Material Index Doc": r"Raw Material Index\s+Doc No\.\s*:\s*([\w\-]+)\s+Rev\.\s*:\s*(\d+)",
                "Plating Inspection Doc": r"Plating inspection Doc No\.\s*:([\w\-]+)\s+Rev\.\s*:\s*(\d+)",
                "MPP Doc": r"MPP Doc No\.\s*:\s*([^R]+)Rev\.\s*:\s*([^\n]+)"
            }

            for key, pattern in doc_patterns.items():
                match = re.search(pattern, long_text)
                if match:
                    if len(match.groups()) == 2:
                        doc_details[key] = {
                            "Number": match.group(1).strip(),
                            "Revision": match.group(2).strip()
                        }
                    else:
                        doc_details[key] = match.group(1).strip()

            data["Document Verification"] = doc_details
            break

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

        with Session(engine) as session:
            master_order = save_to_database(data, session)
            
            # Ensure all relationships are loaded
            session.refresh(master_order)
            
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
                    
                    # Detailed project information
                    "project": {
                        "id": master_order.project.id,
                        "name": master_order.project.name,
                        "priority": master_order.project.priority,
                        "delivery_date": master_order.project.delivery_date,
                        "start_date": master_order.project.start_date,
                        "end_date": master_order.project.end_date
                    } if master_order.project else None,
                    
                    # Detailed raw materials information
                    "raw_materials": [{
                        "id": rm.id,
                        "child_part_number": rm.child_part_number,
                        "description": rm.description,
                        "quantity": rm.quantity,
                        "unit": {
                            "id": rm.unit.id,
                            "name": rm.unit.name
                        } if rm.unit else None,
                        "status": {
                            "id": rm.status.id,
                            "name": rm.status.name
                        } if rm.status else None
                    } for rm in master_order.raw_materials],
                    
                    # Include operations if needed
                    # "operations": [{
                    #     "id": op.id,
                    #     "operation_number": op.operation_number,
                    #     "description": op.operation_description,
                    #     "setup_time": op.setup_time,
                    #     "ideal_cycle_time": op.ideal_cycle_time,
                    #     "work_center": {
                    #         "id": op.work_center.id,
                    #         "code": op.work_center.code,
                    #         "description": op.work_center.description
                    #     } if op.work_center else None
                    # } for op in master_order.operations]
                }
            }

            return response_data
        

    except Exception as e:
        return {"error": str(e)}
    

@router.get("/all_orders", response_model=List[GetAllOrders])
async def get_all_orders():
    try:
        orders = await getAllOrders()
        if not orders:
            return []
        return orders
    except Exception as e:
        return {"error": str(e)}



def save_to_database(data, session):
    try:
        # Check if an order with the same production_order already exists
        existing_order = session.query(Order).filter_by(
            production_order=data["Prod Order No"]
        ).first()

        if existing_order:
            session.refresh(existing_order)
            return existing_order

        # Create or get project first
        project = session.query(Project).filter_by(name=data["Project Name"]).first()
        if not project:
            try:
                project = Project(
                    name=data["Project Name"],
                    priority=1,
                    start_date=None,
                    end_date=None,
                    delivery_date=None
                )
                session.add(project)
                session.flush()
            except Exception as e:
                print(f"Error creating project: {e}")
                raise

        # Create master order with project reference
        master_order = Order(
            production_order=data["Prod Order No"],
            sale_order=data["Sale Order"],
            wbs_element=data["WBS"],
            part_number=data["Part No"],
            part_description=data["Part Desc"],
            total_operations=len(data["Operations"]),
            required_quantity=float(data["Required Qty"]),
            launched_quantity=float(data["Launched Qty"]),
            project_id=project.id,  # Link to project
            plant_id=int(data["Plant"]) if data["Plant"].strip().isdigit() else None
        )
        session.add(master_order)
        session.flush()

        # Get or create default inventory status
        default_status = session.query(InventoryStatus).filter_by(name="Available").first()
        if not default_status:
            default_status = InventoryStatus(
                name="Available",
                description="Material is available for use"
            )
            session.add(default_status)
            session.flush()

        # Insert Document References
        for doc_type, doc_info in data["Document Verification"].items():
            print(f"Processing document: {doc_type} with info: {doc_info}")

            existing_document = session.query(Documents).filter_by(
                order=master_order,
                document_name=doc_type,
                type=doc_type
            ).first()

            if existing_document:
                print(f"Document already exists: {doc_type}")
                continue

            try:
                document = Documents(
                    order=master_order,
                    document_name=doc_type,
                    type=doc_type,
                    version=doc_info.get("Revision", "--") if isinstance(doc_info, dict) else "--"
                )
                session.add(document)
                print(f"Document added: {doc_type}")

            except Exception as e:
                print(f"Error inserting document {doc_type}: {e}")
                session.rollback()

        # Modified raw materials handling
        raw_materials_list = []
        for raw_mat in data["Raw Materials"]:
            unit = session.query(Unit).filter_by(name=raw_mat["UoM"]).first()
            if not unit:
                unit = Unit(name=raw_mat["UoM"])
                session.add(unit)
                session.flush()

            existing_raw_material = session.query(RawMaterial).filter_by(
                order_id=master_order.id,
                child_part_number=raw_mat["Child Part No"]
            ).first()

            if not existing_raw_material:
                raw_material = RawMaterial(
                    order_id=master_order.id,
                    child_part_number=raw_mat["Child Part No"],
                    description=raw_mat["Description"],
                    quantity=float(raw_mat["Total Qty"]),
                    unit_id=unit.id,
                    status_id=default_status.id
                )
                session.add(raw_material)
                raw_materials_list.append(raw_material)

        # Insert Operations and Work Centers
        for op in data["Operations"]:
            existing_operation = session.query(Operation).filter_by(
                order=master_order,
                operation_number=int(op["Oprn No"])
            ).first()

            if existing_operation:
                continue

            work_center = session.query(WorkCenter).filter_by(code=op["Wc/Plant"]).first()
            if not work_center:
                work_center = WorkCenter(
                    code=op["Wc/Plant"],
                    plant_id=int(op["Plant Number"]) if op["Plant Number"] else None,
                    description=op["Operation"],
                    operation=op["Operation"]
                )
                session.add(work_center)

            operation = Operation(
                order=master_order,
                work_center=work_center,
                operation_number=int(op["Oprn No"]),
                operation_description=op["Operation"],
                setup_time=float(op["Setup Time"]),
                ideal_cycle_time=float(op["Per Pc Time"])
            )
            session.add(operation)

        # Commit all changes
        session.commit()
        
        # Refresh to ensure all relationships are loaded
        session.refresh(master_order)
        return master_order

    except Exception as e:
        session.rollback()
        raise

@router.put("/update_order/{order_id}", response_model=GetAllOrders)
async def edit_order(order_number: str, update_data: OrderUpdate):
    """
    Update an existing order and optionally its project delivery date.
    
    Parameters:
    - order_id: The ID of the order to update
    - update_data: The data to update, including:
        - sale_order: Optional[str]
        - wbs_element: Optional[str]
        - part_number: Optional[str]
        - part_description: Optional[str]
        - total_operations: Optional[int]
        - required_quantity: Optional[float]
        - launched_quantity: Optional[float]
        - plant_id: Optional[int]
        - delivery_date: Optional[int] (epoch timestamp)
    
    Returns:
    - Updated order with its relationships
    
    Note: project_id and raw_materials cannot be modified through this endpoint.
    The delivery_date should be provided as an epoch timestamp and will update
    the associated project's delivery date.
    """
    try:
        updated_order = await update_order(order_number, update_data)
        return updated_order
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search_order", response_model=ResponseModel)
async def search_order(
    part_number: Optional[str] = Query(None, min_length=1),
    part_description: Optional[str] = Query(None, min_length=1)
):
    """
    Get order details by part number or part description.
    - Requires either `part_number` or `part_description`, but not both.
    """
    try:
        # Validate input to ensure either part_number or part_description is provided
        if (part_number and part_description) or (not part_number and not part_description):
            raise HTTPException(
                status_code=400,
                detail="Please provide either a part number or part description, but not both."
            )

        with Session(engine) as session:
            if part_number:
                orders = session.query(Order).filter(
                    Order.part_number.ilike(f"%{part_number}%")
                ).all()
            elif part_description:
                orders = session.query(Order).filter(
                    Order.part_description.ilike(f"%{part_description}%")
                ).all()
            else:
                orders = []

            if not orders:
                # Return an empty list instead of raising an error
                return {"orders": []}

            # Format the orders in a structure matching the response model
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
                            "delivery_date": order.project.delivery_date,
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
                            for op in session.query(Operation).filter_by(order_id=order.id).all()
                        ]
                    }
                    for order in orders
                ]
            }

            return response_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")




@router.put("/operations/{part_number}/{operation_number}", response_model=OperationResponse)
async def update_operation(
    part_number: str,
    operation_number: int,
    operation_data: OperationUpdate
):
    """
    Update a specific operation for a given part number and operation number.
    """
    db = SessionLocal()
    try:
        # Find the order by part number
        order = db.query(Order).filter(Order.part_number == part_number).first()
        if not order:
            raise HTTPException(
                status_code=404,
                detail=f"No order found with part number {part_number}"
            )

        # Find the specific operation for this order
        operation = db.query(Operation).filter(
            Operation.order_id == order.id,
            Operation.operation_number == operation_number
        ).first()
        if not operation:
            raise HTTPException(
                status_code=404,
                detail=f"Operation number {operation_number} not found for part number {part_number}"
            )

        # Update fields in the operation
        if operation_data.operation_description is not None:
            operation.operation_description = operation_data.operation_description
        if operation_data.setup_time is not None:
            operation.setup_time = operation_data.setup_time
        if operation_data.ideal_cycle_time is not None:
            operation.ideal_cycle_time = operation_data.ideal_cycle_time

        # Update work center if provided
        if operation_data.work_center_code:
            work_center = db.query(WorkCenter).filter(
                WorkCenter.code == operation_data.work_center_code
            ).first()
            if not work_center:
                raise HTTPException(
                    status_code=404,
                    detail=f"Work center with code {operation_data.work_center_code} not found"
                )
            operation.work_center_id = work_center.id

        db.commit()
        db.refresh(operation)

        # Prepare response
        response = {
            "id": operation.id,
            "operation_number": operation.operation_number,
            "operation_description": operation.operation_description,
            "setup_time": operation.setup_time,
            "ideal_cycle_time": operation.ideal_cycle_time,
            "work_center": operation.work_center.code if operation.work_center else None
        }

        return response

    except HTTPException as he:
        db.rollback()
        raise he
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.post("/operations", response_model=OperationResponse)
async def create_operation(operation_data: OperationCreate):
    """
    Create a new operation for an existing order
    """
    db = SessionLocal()
    try:
        # Check if order exists
        order = db.query(Order).filter(Order.id == operation_data.order_id).first()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        # Check if work center exists
        work_center = db.query(WorkCenter).filter(
            WorkCenter.code == operation_data.work_center_code
        ).first()
        if not work_center:
            raise HTTPException(
                status_code=404,
                detail=f"Work center with code {operation_data.work_center_code} not found"
            )

        # Check if operation number already exists for this order
        existing_op = db.query(Operation).filter(
            Operation.order_id == operation_data.order_id,
            Operation.operation_number == operation_data.operation_number
        ).first()
        if existing_op:
            raise HTTPException(
                status_code=400,
                detail=f"Operation number {operation_data.operation_number} already exists for this order"
            )

        # Create new operation
        new_operation = Operation(
            order_id=operation_data.order_id,
            operation_number=operation_data.operation_number,
            operation_description=operation_data.operation_description,
            setup_time=operation_data.setup_time,
            ideal_cycle_time=operation_data.ideal_cycle_time,
            work_center_id=work_center.id,
            part_number=operation_data.part_number  # Include part_number here
        )

        db.add(new_operation)
        db.commit()
        db.refresh(new_operation)

        # Update total_operations count in the order
        order.total_operations = db.query(Operation).filter(
            Operation.order_id == order.id
        ).count()
        db.commit()

        # Prepare response
        response = {
            "id": new_operation.id,
            "operation_number": new_operation.operation_number,
            "operation_description": new_operation.operation_description,
            "setup_time": new_operation.setup_time,
            "ideal_cycle_time": new_operation.ideal_cycle_time,
            "work_center": work_center.code,
            "part_number": new_operation.part_number  # Include part_number in response
        }

        return response

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.post("/create_order", response_model=GetAllOrders, status_code=201)
async def create_new_order(
    order_data: OrderCreate,
    db: Session = Depends(get_db)
):
    """
    Create a new order with project linking.

    Parameters:
    - production_order: Required, unique identifier for the order
    - project_name: Required, name of the project to link or create
    - delivery_date: Optional epoch timestamp for project delivery date
    - Other optional order details

    Returns:
    - Created order with project details

    Raises:
    - 400: Invalid input data
    - 409: Order already exists
    - 500: Server error
    """
    try:
        # Validate production order format if needed
        if not order_data.production_order.strip():
            raise HTTPException(
                status_code=400,
                detail="Production order cannot be empty"
            )

        # Create order
        new_order = await create_order(order_data, db)

        if not new_order:
            raise HTTPException(
                status_code=500,
                detail="Failed to create order"
            )

        return new_order

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )


@router.get("/work_centers", response_model=List[WorkCenterResponse])
async def get_work_centers():
    """
    Get a list of all work centers with their IDs and codes.
    """
    db = SessionLocal()
    try:
        # Query all work centers
        work_centers = db.query(WorkCenter).all()

        # Prepare response
        response = [{"id": wc.id, "code": wc.code} for wc in work_centers]

        return response

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
