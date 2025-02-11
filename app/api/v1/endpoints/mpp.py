from fastapi import APIRouter, HTTPException
from typing import List, Dict, Optional
from pony.orm import db_session, select, commit
from datetime import datetime
from app.models.master_order import MPP, Operation, Order
from app.schemas.mpp import MPPResponse, NewMPPCreate, UpdateMPP

router = APIRouter()

@router.get("/mpp/by-identifier", response_model=List[MPPResponse])
async def get_mpp_by_identifier(
    operation_number: int,
    part_number: Optional[str] = None,
    production_order: Optional[str] = None,
):
    """Get all MPP entries for a specific part number or production order and operation number combination"""
    try:
        if part_number is None and production_order is None:
            raise HTTPException(
                status_code=400,
                detail="Either part_number or production_order must be provided"
            )
        if part_number and production_order:
            raise HTTPException(
                status_code=400,
                detail="Only one of part_number or production_order should be provided"
            )

        with db_session:
            if part_number:
                order = Order.get(part_number=part_number)
            else:
                order = Order.get(production_order=production_order)

            if not order:
                raise HTTPException(
                    status_code=404,
                    detail=f"No order found with provided identifier"
                )

            operation = select(op for op in Operation
                               if op.order == order and
                               op.operation_number == operation_number).first()
            if not operation:
                raise HTTPException(
                    status_code=404,
                    detail=f"No operation found with number {operation_number}"
                )

            mpps = select(m for m in MPP if m.order == order and m.operation == operation)[:]

            if not mpps:
                raise HTTPException(
                    status_code=404,
                    detail=f"No MPP found for the given identifiers"
                )

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
                    "part_number": order.part_number,
                    "production_order": order.production_order,
                    "operation_number": operation.operation_number
                }
                response_data.append(MPPResponse(**mpp_data))

            return response_data

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving MPP: {str(e)}"
        )


@router.post("/mpp", response_model=MPPResponse)
async def create_new_mpp(mpp_data: NewMPPCreate):
    """Create a new MPP entry using either part_number or production_order"""
    try:
        with db_session:
            if mpp_data.part_number and mpp_data.production_order:
                raise HTTPException(
                    status_code=400,
                    detail="Only one of part_number or production_order should be provided"
                )

            if not mpp_data.part_number and not mpp_data.production_order:
                raise HTTPException(
                    status_code=400,
                    detail="Either part_number or production_order must be provided"
                )

            if mpp_data.part_number:
                order = Order.get(part_number=mpp_data.part_number)
            else:
                order = Order.get(production_order=mpp_data.production_order)

            if not order:
                raise HTTPException(
                    status_code=404,
                    detail=f"No order found with provided identifier"
                )

            operation = select(op for op in Operation
                               if op.order == order and
                               op.operation_number == mpp_data.operation_number).first()
            if not operation:
                raise HTTPException(
                    status_code=404,
                    detail=f"No operation found with number {mpp_data.operation_number}"
                )

            existing_mpp = select(m for m in MPP
                                  if m.order == order and
                                  m.operation == operation).first()

            if existing_mpp:
                current_instructions = existing_mpp.work_instructions
                next_sequence = len(current_instructions["sections"])

                for section in mpp_data.work_instructions:
                    current_instructions["sections"].append({
                        "title": section.title,
                        "instructions": section.instructions,
                        "sequence": next_sequence
                    })
                    next_sequence += 1

                existing_mpp.fixture_number = mpp_data.fixture_number
                existing_mpp.ipid_number = mpp_data.ipid_number
                existing_mpp.datum_x = mpp_data.datum_x
                existing_mpp.datum_y = mpp_data.datum_y
                existing_mpp.datum_z = mpp_data.datum_z
                existing_mpp.work_instructions = current_instructions

                commit()

                response_data = {
                    "id": existing_mpp.id,
                    "order_id": order.id,
                    "operation_id": operation.id,
                    "document_id": existing_mpp.document.id if existing_mpp.document else None,
                    "fixture_number": existing_mpp.fixture_number,
                    "ipid_number": existing_mpp.ipid_number,
                    "datum_x": existing_mpp.datum_x,
                    "datum_y": existing_mpp.datum_y,
                    "datum_z": existing_mpp.datum_z,
                    "work_instructions": existing_mpp.work_instructions,
                    "part_number": order.part_number,
                    "production_order": order.production_order,
                    "operation_number": operation.operation_number
                }

                return MPPResponse(**response_data)

            new_mpp = MPP(
                order=order,
                operation=operation,
                fixture_number=mpp_data.fixture_number,
                ipid_number=mpp_data.ipid_number,
                datum_x=mpp_data.datum_x,
                datum_y=mpp_data.datum_y,
                datum_z=mpp_data.datum_z,
                work_instructions={
                    "sections": [
                        {
                            "title": section.title,
                            "instructions": section.instructions,
                            "sequence": idx
                        }
                        for idx, section in enumerate(mpp_data.work_instructions)
                    ]
                }
            )
            commit()

            response_data = {
                "id": new_mpp.id,
                "order_id": order.id,
                "operation_id": operation.id,
                "document_id": new_mpp.document.id if new_mpp.document else None,
                "fixture_number": new_mpp.fixture_number,
                "ipid_number": new_mpp.ipid_number,
                "datum_x": new_mpp.datum_x,
                "datum_y": new_mpp.datum_y,
                "datum_z": new_mpp.datum_z,
                "work_instructions": new_mpp.work_instructions,
                "part_number": order.part_number,
                "production_order": order.production_order,
                "operation_number": operation.operation_number
            }

            return MPPResponse(**response_data)

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error creating MPP: {str(e)}"
        )


@router.put("/mpp/{part_number}/{operation_number}", response_model=MPPResponse)
async def update_mpp(
        part_number: str,
        operation_number: int,
        update_data: UpdateMPP,
):
    """Update all fields of an MPP entry based on part number and operation number"""
    try:
        with db_session:
            # Verify part number in path matches body
            if part_number != update_data.part_number:
                raise HTTPException(
                    status_code=400,
                    detail="Part number in path must match part number in request body"
                )

            # Verify operation number in path matches body
            if operation_number != update_data.operation_number:
                raise HTTPException(
                    status_code=400,
                    detail="Operation number in path must match operation number in request body"
                )

            order = Order.get(part_number=part_number)
            if not order:
                raise HTTPException(
                    status_code=404,
                    detail=f"No order found with part number {part_number}"
                )

            operation = select(op for op in Operation
                               if op.order == order and
                               op.operation_number == operation_number).first()
            if not operation:
                raise HTTPException(
                    status_code=404,
                    detail=f"No operation found with number {operation_number}"
                )

            mpp = select(m for m in MPP
                         if m.order == order and
                         m.operation == operation).first()
            if not mpp:
                raise HTTPException(
                    status_code=404,
                    detail=f"No MPP found for part number {part_number} and operation {operation_number}"
                )

            # Update all MPP fields
            mpp.fixture_number = update_data.fixture_number
            mpp.ipid_number = update_data.ipid_number
            mpp.datum_x = update_data.datum_x
            mpp.datum_y = update_data.datum_y
            mpp.datum_z = update_data.datum_z

            # Update work instructions with new sections
            mpp.work_instructions = {
                "sections": [
                    {
                        "title": section.title,
                        "instructions": section.instructions,
                        "sequence": idx
                    }
                    for idx, section in enumerate(update_data.work_instructions)
                ]
            }

            # Update order's production order if it has changed
            if order.production_order != update_data.production_order:
                order.production_order = update_data.production_order

            commit()

            response_data = {
                "id": mpp.id,
                "order_id": order.id,
                "operation_id": operation.id,
                "document_id": mpp.document.id if mpp.document else None,
                "fixture_number": mpp.fixture_number,
                "ipid_number": mpp.ipid_number,
                "datum_x": mpp.datum_x,
                "datum_y": mpp.datum_y,
                "datum_z": mpp.datum_z,
                "work_instructions": mpp.work_instructions,
                "part_number": order.part_number,
                "production_order": order.production_order,
                "operation_number": operation.operation_number
            }

            return MPPResponse(**response_data)

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating MPP: {str(e)}"
        )