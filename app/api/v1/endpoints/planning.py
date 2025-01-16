from fastapi import FastAPI, File, UploadFile, APIRouter, HTTPException
from pony.orm import db_session, select
from datetime import datetime
from typing import List
import PyPDF2
import io
import re

from app.database.connection import db
from app.models import (
    WorkCenter, Machine, Project, Order, Operation,
    ProcessPlan, Document, ToolList, JigsAndFixturesList,
    Unit, RawMaterial, InventoryStatus
)

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
            project = Project(
                name=data["Project Name"],
                priority=1,
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
            work_center = WorkCenter.get(code=op["Wc/Plant"])
            if not work_center:
                work_center = WorkCenter(
                    code=op["Wc/Plant"],
                    plant_id=op["Plant Number"] or "0",
                    work_center_name=op["Operation"],
                    description=op["Operation"]
                )

            # Create a default machine for the operation
            machine = Machine.get(work_center=work_center, type="Default")
            if not machine:
                machine = Machine(
                    work_center=work_center,
                    type="Default",
                    make="Default",
                    model="Default"
                )

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


