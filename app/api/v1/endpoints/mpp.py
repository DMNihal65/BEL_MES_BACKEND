from fastapi import APIRouter, HTTPException
from typing import List
from pony.orm import db_session, select, commit
from app.models import Document
from app.models.master_order import MPP, Operation, Order
from app.models.document_management import DocType
from app.schemas.mpp import MPPResponse, NewMPPCreate, UpdateMPPSections, MPPUpdateResponse, MPPUpdateRequest

router = APIRouter(prefix="/api/v1", tags=["mpp"])


@router.get("/mpp/by-part/{part_number}/{operation_number}", response_model=List[MPPResponse])
async def get_mpp_by_part(part_number: str, operation_number: int):
    """Get all MPP entries for a specific part number and operation number combination"""
    with db_session:
        # Find all orders matching the part number
        orders = list(select(o for o in Order if o.part_number == part_number))

        if not orders:
            raise HTTPException(404, detail=f"No order found with part number: {part_number}")

        # If multiple orders, use the first one
        if len(orders) > 1:
            print(f"Warning: Multiple orders found for part number {part_number}")

        order = orders[0]

        # Find the operation
        operation = select(op for op in Operation
                           if op.order == order and
                           op.operation_number == operation_number).first()
        if not operation:
            raise HTTPException(404, detail=f"No operation found with number {operation_number}")

        # Get MPP entries
        mpps = select(m for m in MPP if m.order == order and m.operation == operation)[:]

        if not mpps:
            raise HTTPException(404,
                                detail=f"No MPP found for part number: {part_number} and operation number: {operation_number}")

        # Prepare response
        response_data = []
        for mpp in mpps:
            mpp_data = {
                "id": mpp.id,
                "order_id": mpp.order.id,
                "operation_id": mpp.operation.id,
                "document_id": mpp.document.id if mpp.document else None,
                "fixture_number": mpp.fixture_number,
                "ipid_number": mpp.ipid_number,
                "datum_x": mpp.datum_x,
                "datum_y": mpp.datum_y,
                "datum_z": mpp.datum_z,
                "work_instructions": mpp.work_instructions,
                "part_number": mpp.order.part_number,
                "operation_number": mpp.operation.operation_number
            }
            response_data.append(MPPResponse(**mpp_data))

        return response_data


@router.post("/mpp", response_model=MPPResponse)
async def create_new_mpp(mpp_data: NewMPPCreate):
    """Create a new MPP entry"""
    try:
        with db_session:
            # Find all orders matching the part number
            orders = list(select(o for o in Order if o.part_number == mpp_data.part_number))

            if not orders:
                raise HTTPException(404, detail=f"No order found with part number: {mpp_data.part_number}")

            # If multiple orders, use the first one
            if len(orders) > 1:
                print(f"Warning: Multiple orders found for part number {mpp_data.part_number}")

            order = orders[0]

            # Find operation
            operation = select(op for op in Operation
                               if op.order == order and
                               op.operation_number == mpp_data.operation_number).first()
            if not operation:
                raise HTTPException(404, detail=f"No operation found with number {mpp_data.operation_number}")

            # Find MPP document if exists
            doc_type = select(dt for dt in DocType if dt.type_name == "MPP").first()

            # Get all matching documents
            matching_docs = list(select(d for d in Document
                                        if d.part_number_id == order and
                                        d.doc_type == doc_type))

            # Handle multiple documents
            document = matching_docs[0] if matching_docs else None

            # Check if MPP already exists
            existing_mpp = select(m for m in MPP
                                  if m.order == order and
                                  m.operation == operation).first()

            if existing_mpp:
                # Update existing MPP
                current_instructions = existing_mpp.work_instructions
                next_sequence = len(current_instructions["sections"])

                # Add new sections
                for section in mpp_data.work_instructions:
                    current_instructions["sections"].append({
                        "title": section.title,
                        "instructions": section.instructions,
                        "sequence": next_sequence
                    })
                    next_sequence += 1

                # Update MPP
                existing_mpp.fixture_number = mpp_data.fixture_number
                existing_mpp.ipid_number = mpp_data.ipid_number
                existing_mpp.datum_x = mpp_data.datum_x
                existing_mpp.datum_y = mpp_data.datum_y
                existing_mpp.datum_z = mpp_data.datum_z
                existing_mpp.work_instructions = current_instructions

                commit()

                return MPPResponse(
                    id=existing_mpp.id,
                    order_id=order.id,
                    operation_id=operation.id,
                    document_id=document.id if document else None,
                    fixture_number=existing_mpp.fixture_number,
                    ipid_number=existing_mpp.ipid_number,
                    datum_x=existing_mpp.datum_x,
                    datum_y=existing_mpp.datum_y,
                    datum_z=existing_mpp.datum_z,
                    work_instructions=existing_mpp.work_instructions,
                    part_number=order.part_number,
                    operation_number=operation.operation_number
                )

            else:
                # Create new MPP
                work_instructions = {
                    "sections": [
                        {
                            "title": section.title,
                            "instructions": section.instructions,
                            "sequence": idx
                        }
                        for idx, section in enumerate(mpp_data.work_instructions)
                    ]
                }

                new_mpp = MPP(
                    order=order,
                    operation=operation,
                    document=document,
                    fixture_number=mpp_data.fixture_number,
                    ipid_number=mpp_data.ipid_number,
                    datum_x=mpp_data.datum_x,
                    datum_y=mpp_data.datum_y,
                    datum_z=mpp_data.datum_z,
                    work_instructions=work_instructions
                )

                commit()

                return MPPResponse(
                    id=new_mpp.id,
                    order_id=order.id,
                    operation_id=operation.id,
                    document_id=document.id if document else None,
                    fixture_number=new_mpp.fixture_number,
                    ipid_number=new_mpp.ipid_number,
                    datum_x=new_mpp.datum_x,
                    datum_y=new_mpp.datum_y,
                    datum_z=new_mpp.datum_z,
                    work_instructions=new_mpp.work_instructions,
                    part_number=order.part_number,
                    operation_number=operation.operation_number
                )

    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(500, detail=str(e))


@router.put("/mpp/by-part/{part_number}/{operation_number}", response_model=MPPUpdateResponse)
async def update_mpp(part_number: str, operation_number: int, mpp_data: MPPUpdateRequest):
    """Update an existing MPP entry for a specific part number and operation number combination"""
    try:
        with db_session:
            # Find all orders matching the part number
            orders = list(select(o for o in Order if o.part_number == part_number))

            if not orders:
                raise HTTPException(404, detail=f"No order found with part number: {part_number}")

            # If multiple orders, use the first one
            if len(orders) > 1:
                print(f"Warning: Multiple orders found for part number {part_number}")

            order = orders[0]

            # Find operation
            operation = select(op for op in Operation
                               if op.order == order and
                               op.operation_number == operation_number).first()
            if not operation:
                raise HTTPException(404, detail=f"No operation found with number {operation_number}")

            # Find existing MPP
            mpp = select(m for m in MPP
                         if m.order == order and
                         m.operation == operation).first()
            if not mpp:
                raise HTTPException(404,
                                    detail=f"No MPP found for part number: {part_number} and operation number: {operation_number}")

            # Find MPP document if exists
            doc_type = select(dt for dt in DocType if dt.type_name == "MPP").first()

            # Get all matching documents
            matching_docs = list(select(d for d in Document
                                        if d.part_number_id == order and
                                        d.doc_type == doc_type))

            # Handle multiple documents
            document = matching_docs[0] if matching_docs else None

            # Update work instructions
            work_instructions = {
                "sections": [
                    {
                        "title": section.title,
                        "instructions": section.instructions,
                        "sequence": idx
                    }
                    for idx, section in enumerate(mpp_data.work_instructions)
                ]
            }

            # Update MPP fields
            mpp.fixture_number = mpp_data.fixture_number
            mpp.ipid_number = mpp_data.ipid_number
            mpp.datum_x = mpp_data.datum_x
            mpp.datum_y = mpp_data.datum_y
            mpp.datum_z = mpp_data.datum_z
            mpp.work_instructions = work_instructions

            commit()

            return MPPUpdateResponse(
                id=mpp.id,
                order_id=order.id,
                operation_id=operation.id,
                document_id=document.id if document else None,
                fixture_number=mpp.fixture_number,
                ipid_number=mpp.ipid_number,
                datum_x=mpp.datum_x,
                datum_y=mpp.datum_y,
                datum_z=mpp.datum_z,
                work_instructions=mpp.work_instructions
            )

    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(500, detail=str(e))