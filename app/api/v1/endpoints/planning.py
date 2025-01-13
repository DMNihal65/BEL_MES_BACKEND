from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from typing import Dict, Any
import PyPDF2
import io
from datetime import datetime
from pony.orm import db_session, commit

from app.utils.oarc_extractor import extract_oarc_details
from app.models.master_order import Project, Order, Operation, WorkCenter

router = APIRouter()

@router.post("/upload-oarc/", 
    response_model=Dict[str, Any],
    summary="Upload and extract OARC details",
    description="Upload an OARC PDF file and extract all relevant details from it",
    tags=["planning"])
async def upload_oarc(
    file: UploadFile = File(...),
) -> Dict[str, Any]:
    """Upload and process OARC file, saving data to database"""
    if not file.filename.endswith('.pdf'):
        raise HTTPException(
            status_code=400,
            detail="Invalid file format. Please upload a PDF file"
        )

    try:
        # Read and extract data from PDF
        contents = await file.read()
        pdf_file = io.BytesIO(contents)
        extracted_data = extract_oarc_details(pdf_file)

        with db_session:
            # First check if order already exists
            existing_order = Order.get(production_order=extracted_data["Prod Order No"])
            if existing_order:
                raise HTTPException(
                    status_code=400,
                    detail="Production order already exists"
                )

            # Create or get project
            project = Project.get(name=extracted_data["Project Name"])
            if not project:
                project = Project(
                    name=extracted_data["Project Name"],
                    priority=1,  # Default priority
                    start_date=datetime.now(),  # Default to current date
                    end_date=datetime.now(),  # Should be set properly
                )

            # Create order
            order = Order(
                production_order=extracted_data["Prod Order No"],
                sale_order=extracted_data.get("Sale Order"),
                wbs_element=extracted_data.get("WBS"),
                part_number=extracted_data["Part No"],
                part_description=extracted_data.get("Part Desc"),
                total_operations=len(extracted_data["Operations"]),
                required_quantity=int(extracted_data.get("Required Qty", 0)),
                launched_quantity=int(extracted_data.get("Launched Qty", 0)),
                raw_material="",  # Empty string instead of None
                plant_id=extracted_data.get("Plant", ""),
                delivery_date=datetime.now(),  # Should be set properly
                project=project
            )

            # Create operations
            for op_data in extracted_data["Operations"]:
                # Get or create work center
                work_center = WorkCenter.get(code=op_data["Wc/Plant"])
                if not work_center:
                    work_center = WorkCenter(
                        code=op_data["Wc/Plant"],
                        plant_id=op_data.get("Plant Number", ""),
                        description=op_data.get("Operation", ""),
                        operation=op_data.get("Operation", "")
                    )

                # Create operation
                operation = Operation(
                    order=order,
                    operation_number=int(op_data["Oprn No"]),
                    work_center=work_center,
                    operation_description=op_data.get("Operation", ""),
                    setup_time=float(op_data.get("Setup Time", 0)),
                    ideal_cycle_time=float(op_data.get("Per Pc Time", 0))
                )

            commit()
            
            return {
                "message": "OARC processed successfully",
                "production_order": order.production_order,
                "project_name": project.name
            }

    except PyPDF2.errors.PdfReadError:
        raise HTTPException(
            status_code=400,
            detail="Invalid PDF file or file is corrupted"
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Error processing data: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error processing OARC file: {str(e)}"
        ) 