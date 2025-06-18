from fastapi import APIRouter, HTTPException, Depends, Path, Query
from typing import Any, List
import os
import subprocess
from pony.orm import db_session, commit, flush, select, desc

from app.core.security import get_current_user
from app.models import Operation, Order, User
from app.schemas.quality import MasterBocCreate, MasterBocResponse, StageInspectionResponse, \
    StageInspectionCreate, QualityInspectionResponse, DetailedQualityInspectionResponse, \
    OrderIPIDResponse, MasterBocIPIDInfo, MeasurementInstrumentsResponse, \
    ConnectivityCreate, ConnectivityResponse, StageInspectionDetail, FTPResponse, \
    StageInspectionWithUserResponse, OperatorInfo
from app.crud.quality import MasterBocCRUD, StageInspectionCRUD, QualityInspectionCRUD, FTPCRUD
from app.models.quality import Connectivity, StageInspection
from app.models.inventoryv1 import InventoryItem

from app.schemas.quality import OperatorInfo, StageInspectionWithOperator, OperationGroup

router = APIRouter(prefix="/api/v1/quality", tags=["quality"])


@router.post(
    "/master-boc/",
    response_model=MasterBocResponse,
    status_code=201
)
async def create_master_boc(
        data: MasterBocCreate,
        current_user=Depends(get_current_user)
) -> Any:
    """
    Create a new Master BOC entry

    The bbox field must contain exactly 8 values representing the coordinates:
    [x1, y1, x2, y2, x3, y3, x4, y4]

    Example request body:
    ```json
    {
        "order_id": 1,
        "document_id": 1,
        "nominal": "10.5",
        "uppertol": 0.1,
        "lowertol": -0.1,
        "zone": "A",
        "dimension_type": "diameter",
        "measured_instrument": "caliper",
        "op_no": 10,
        "bbox": [100.0, 200.0, 300.0, 200.0, 300.0, 400.0, 100.0, 400.0],
        "ipid": "IP123"
    }
    ```
    """
    try:
        print(f"Received bbox data: {data.bbox}")  # Debug log
        master_boc = MasterBocCRUD.create_master_boc(data)
        print(f"Response bbox: {master_boc.bbox}")  # Debug log
        return master_boc
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Error creating Master BOC: {str(e)}"
        )


@router.get(
    "/master-boc/measurement-instruments",
    response_model=MeasurementInstrumentsResponse,
    summary="Get all measurement instruments"
)
async def get_measurement_instruments(
        current_user=Depends(get_current_user)
) -> Any:
    """
    Get a list of all unique measurement instruments used in Master BOC entries
    """
    try:
        instruments = MasterBocCRUD.get_all_measurement_instruments()
        return MeasurementInstrumentsResponse(instruments=instruments)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Error retrieving measurement instruments: {str(e)}"
        )


@router.get(
    "/master-boc/{id}",
    response_model=MasterBocResponse
)
async def get_master_boc(
        id: int = Path(..., gt=0),
        current_user=Depends(get_current_user)
) -> Any:
    """Get Master BOC by ID"""
    try:
        master_boc = MasterBocCRUD.get_master_boc(id)
        if not master_boc:
            raise HTTPException(status_code=404, detail="Master BOC not found")
        return master_boc
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/master-boc/order/{order_id}",
    response_model=List[MasterBocResponse]
)
async def get_master_bocs_by_order(
        order_id: int = Path(..., gt=0),
        op_no: int = Query(..., gt=0),
        measurement_instruments: List[str] = Query(None, description="Filter by multiple measurement instruments"),
        current_user=Depends(get_current_user)
) -> Any:
    """
    Get all Master BOCs for an order and operation number
    Optionally filter by multiple measurement instruments
    """
    try:
        master_bocs = MasterBocCRUD.get_by_order_and_op_no(
            order_id,
            op_no,
            measurement_instruments
        )
        return master_bocs
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/stage-inspection/",
    response_model=StageInspectionResponse,
    status_code=201
)
async def create_stage_inspection(
        data: StageInspectionCreate,
        current_user=Depends(get_current_user)
) -> Any:
    """
    Create a new Stage Inspection entry

    This endpoint enforces the following validation rules:
    - For quantity 1: Creates FTP status entries for all related IPIDs (initially set to not completed)
    - For quantity > 1: Verifies that quantity 1 exists
    - For quantity > 1: Verifies that FTP approval is completed for quantity 1

    You will receive an error if you attempt to create quantities > 1 before
    FTP approval for quantity 1 is completed.
    """
    try:
        stage_inspection = StageInspectionCRUD.create_stage_inspection(data)
        return stage_inspection
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error creating Stage Inspection: {str(e)}"
        )


@router.patch(
    "/stage-inspection/{inspection_id}/status",
    response_model=StageInspectionResponse,
    summary="Update the FTP status for a stage inspection"
)
async def update_inspection_status(
        inspection_id: int = Path(..., gt=0, description="Stage inspection ID"),
        is_completed: bool = Query(..., description="Completion status for FTP"),
        current_user=Depends(get_current_user)
) -> Any:
    """
    Update the FTP completion status for a stage inspection.

    This endpoint updates FTP status for related IPIDs based on the inspection.
    """
    try:
        updated_inspection = StageInspectionCRUD.update_inspection_status(inspection_id, is_completed)
        return updated_inspection
    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating inspection status: {str(e)}"
        )


@router.get(
    "/inspection/{order_id}/detailed",
    response_model=DetailedQualityInspectionResponse
)
async def get_detailed_quality_inspection(
        order_id: int = Path(..., gt=0),
        current_user=Depends(get_current_user)
) -> Any:
    """
    Get detailed quality inspection data including:
    - Order information (production order, part number)
    - List of all operation numbers
    - Stage inspections grouped by operation number
    - Operator information for each inspection
    """
    try:
        inspection_data = QualityInspectionCRUD.get_detailed_inspection_data(order_id)
        return inspection_data
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Error retrieving detailed quality inspection data: {str(e)}"
        )


@router.get(
    "/stage-inspection/{order_id}/grouped",
    response_model=DetailedQualityInspectionResponse,
    summary="Get stage inspection data grouped by operation number"
)
@db_session
def get_stage_inspection_grouped(
        order_id: int = Path(..., gt=0),
        current_user=Depends(get_current_user)
) -> Any:
    """
    Get stage inspection data grouped by operation number including:
    - Order information (production order, part number)
    - List of all operation numbers
    - Stage inspections grouped by operation number
    - Operator information for each inspection
    """
    try:
        # Get order information
        order = Order.get(id=order_id)
        if not order:
            raise HTTPException(status_code=404, detail=f"Order with ID {order_id} not found")

        # Get all operations for this order
        operations = select(op for op in Operation if op.order.id == order_id).order_by(
            Operation.operation_number)[:]

        if not operations:
            raise HTTPException(status_code=404, detail=f"No operations found for order {order_id}")

        # Get all operation numbers
        operation_numbers = [op.operation_number for op in operations]

        inspection_groups = []

        # Process each operation that has inspections
        for op in operations:
            # Get stage inspections for this operation
            stage_inspections = select(si for si in StageInspection
                                       if si.order_id == order_id and
                                       si.op_no == op.operation_number)[:]

            if stage_inspections:  # Only add to inspection_data if there are inspections
                inspection_list = []
                for si in stage_inspections:
                    # Get operator information
                    operator = User.get(id=si.op_id)
                    if operator:
                        operator_info = OperatorInfo(
                            id=operator.id,
                            username=operator.username,
                            email=operator.email
                        )

                        inspection_list.append(
                            StageInspectionWithOperator(
                                id=si.id,
                                nominal_value=si.nominal_value,
                                uppertol=si.uppertol,
                                lowertol=si.lowertol,
                                zone=si.zone,
                                dimension_type=si.dimension_type,
                                measured_1=si.measured_1,
                                measured_2=si.measured_2,
                                measured_3=si.measured_3,
                                measured_mean=si.measured_mean,
                                measured_instrument=si.measured_instrument,
                                used_inst=si.used_inst,
                                is_done=si.is_done,
                                quantity_no=si.quantity_no,
                                created_at=si.created_at,
                                operator=operator_info
                            )
                        )

                if inspection_list:
                    inspection_groups.append(
                        OperationGroup(
                            operation_number=op.operation_number,
                            inspections=inspection_list
                        )
                    )

        # Create and return the response
        response = DetailedQualityInspectionResponse(
            order_id=order.id,
            production_order=order.production_order,
            part_number=order.part_number,
            operations=operation_numbers,
            inspection_data=inspection_groups
        )

        return response

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Error retrieving stage inspection data: {str(e)}"
        )


@router.get(
    "/master-boc/ipids/{order_id}",
    response_model=OrderIPIDResponse
)
async def get_order_ipids(
        order_id: int = Path(..., gt=0),
        current_user=Depends(get_current_user)
) -> Any:
    """
    Get all IPIDs for an order including:
    - Order information (production order, part number)
    - List of IPIDs with their operation numbers and zones
    """
    try:
        ipid_data = MasterBocCRUD.get_ipids_by_order(order_id)
        return ipid_data
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Error retrieving IPID data: {str(e)}"
        )


@router.post(
    "/connectivity/",
    response_model=ConnectivityResponse,
    status_code=201,
    summary="Create a new connectivity record"
)
@db_session
def create_connectivity(
        data: ConnectivityCreate,
        current_user=Depends(get_current_user)
) -> Any:
    """
    Create a new connectivity record for an inventory item.

    Parameters:
    - inventory_item_id: ID of the inventory item
    - instrument: Name of the instrument
    - uuid: Unique identifier for the connectivity
    - address: Address of the instrument

    Returns:
    - The created connectivity record

    Example request body:
    ```json
    {
        "inventory_item_id": 1,
        "instrument": "Caliper-01",
        "uuid": "550e8400-e29b-41d4-a716-446655440000",
        "address": "192.168.1.100"
    }
    ```
    """
    try:
        # Verify that the inventory item exists
        inventory_item = InventoryItem.get(id=data.inventory_item_id)
        if not inventory_item:
            raise HTTPException(
                status_code=404,
                detail=f"Inventory item with ID {data.inventory_item_id} not found"
            )

        # Create new connectivity record
        new_connectivity = Connectivity(
            inventory_item=inventory_item,
            instrument=data.instrument,
            uuid=data.uuid,
            address=data.address
        )

        # Flush to get the ID and created_at
        flush()

        # Create response object using dict
        response_data = {
            "id": new_connectivity.id,
            "inventory_item_id": inventory_item.id,
            "instrument": new_connectivity.instrument,
            "uuid": new_connectivity.uuid,
            "address": new_connectivity.address,
            "created_at": new_connectivity.created_at
        }

        commit()
        return response_data

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while creating connectivity record: {str(e)}"
        )


@router.get(
    "/connectivity/instrument/{instrument_name}",
    response_model=ConnectivityResponse,
    summary="Get most recent connectivity information by instrument name"
)
@db_session
def get_connectivity_by_instrument(
        instrument_name: str = Path(..., description="Name of the instrument to search for"),
        current_user=Depends(get_current_user)
) -> Any:
    """
    Get the most recent connectivity information for a specific instrument by its name.

    Parameters:
    - instrument_name: Name of the instrument to search for

    Returns:
    - Most recent connectivity information including address and UUID

    Example response:
    ```json
    {
        "id": 1,
        "inventory_item_id": 1,
        "instrument": "Caliper-01",
        "uuid": "550e8400-e29b-41d4-a716-446655440000",
        "address": "192.168.1.100",
        "created_at": "2024-03-20T10:30:00Z"
    }
    ```
    """
    try:
        # Query the most recent connectivity record by instrument name
        connectivity = select(c for c in Connectivity if c.instrument == instrument_name).order_by(
            lambda c: desc(c.created_at)).first()

        if not connectivity:
            raise HTTPException(
                status_code=404,
                detail=f"No connectivity record found for instrument: {instrument_name}"
            )

        # Create response data
        response_data = {
            "id": connectivity.id,
            "inventory_item_id": connectivity.inventory_item.id,
            "instrument": connectivity.instrument,
            "uuid": connectivity.uuid,
            "address": connectivity.address,
            "created_at": connectivity.created_at
        }

        return response_data

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while retrieving connectivity information: {str(e)}"
        )


@router.post(
    "/ftp/{order_id}/{ipid}/update",
    response_model=FTPResponse,
    summary="Update FTP status for an IPID"
)
async def update_ftp_status(
        order_id: int = Path(..., gt=0),
        ipid: str = Path(..., min_length=1),
        current_user=Depends(get_current_user)
) -> Any:
    """
    Update the FTP status for a given order_id and IPID.

    This endpoint explicitly sets the FTP status to completed (is_completed=true).
    FTP status must be completed before adding quantities > 1.
    """
    try:
        ftp_status = FTPCRUD.update_ftp_status(order_id, ipid)
        return ftp_status
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating FTP status: {str(e)}"
        )


@router.get(
    "/ftp/{order_id}/{ipid}",
    response_model=FTPResponse,
    summary="Get FTP status for an IPID"
)
async def get_ftp_status(
        order_id: int = Path(..., gt=0),
        ipid: str = Path(..., min_length=1),
        current_user=Depends(get_current_user)
) -> Any:
    """
    Get the FTP status for a given order_id and IPID
    """
    try:
        ftp_status = FTPCRUD.get_ftp_status(order_id, ipid)
        if not ftp_status:
            raise HTTPException(
                status_code=404,
                detail=f"FTP status not found for order_id {order_id} and IPID {ipid}"
            )
        return ftp_status
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving FTP status: {str(e)}"
        )


@router.get(
    "/ftp/order/{order_id}",
    response_model=List[FTPResponse],
    summary="Get all FTP statuses for an order"
)
async def get_all_ftp_by_order(
        order_id: int = Path(..., gt=0),
        current_user=Depends(get_current_user)
) -> Any:
    """
    Get all FTP statuses for a given order
    """
    try:
        ftp_statuses = FTPCRUD.get_all_ftp_by_order(order_id)
        return ftp_statuses
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving FTP statuses: {str(e)}"
        )


@router.get(
    "/stage-inspection/filter",
    response_model=List[StageInspectionWithUserResponse],
    summary="Get stage inspections by order, quantity and operation number"
)
@db_session
def get_stage_inspections_by_filter(
        order_id: int = Query(..., gt=0, description="Order ID"),
        quantity_no: int = Query(..., gt=0, description="Quantity number"),
        op_no: int = Query(..., gt=0, description="Operation number")
) -> Any:
    """
    Get stage inspection data filtered by order ID, quantity number, and operation number.
    Includes operator (user) details for each inspection.

    Parameters:
    - order_id: ID of the order
    - quantity_no: Quantity number of the inspection
    - op_no: Operation number

    Returns:
    - List of stage inspections with operator details matching the criteria
    """
    try:
        # Query stage inspections with all filters
        stage_inspections = select(si for si in StageInspection
                                   if si.order_id == order_id
                                   and si.quantity_no == quantity_no
                                   and si.op_no == op_no)[:]

        if not stage_inspections:
            raise HTTPException(
                status_code=404,
                detail=f"No stage inspections found for order {order_id}, quantity {quantity_no}, operation {op_no}"
            )

        # Prepare response with user details
        response_data = []
        for si in stage_inspections:
            # Get operator information
            operator = User.get(id=si.op_id)
            operator_info = None
            if operator:
                operator_info = OperatorInfo(
                    id=operator.id,
                    username=operator.username,
                    email=operator.email
                )

            # Create response object
            inspection_data = {
                "id": si.id,
                "op_id": si.op_id,
                "nominal_value": si.nominal_value,
                "uppertol": si.uppertol,
                "lowertol": si.lowertol,
                "zone": si.zone,
                "dimension_type": si.dimension_type,
                "measured_1": si.measured_1,
                "measured_2": si.measured_2,
                "measured_3": si.measured_3,
                "measured_mean": si.measured_mean,
                "measured_instrument": si.measured_instrument,
                "used_inst": si.used_inst,
                "op_no": si.op_no,
                "order_id": si.order_id,
                "quantity_no": si.quantity_no,
                "created_at": si.created_at,
                "operator": operator_info
            }
            response_data.append(StageInspectionWithUserResponse(**inspection_data))

        return response_data

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving stage inspection data: {str(e)}"
        )

