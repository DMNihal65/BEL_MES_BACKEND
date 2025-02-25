from fastapi import APIRouter, HTTPException, Depends, Path, Query
from typing import Any, List

from ....core.security import get_current_user
from ....schemas.quality_schema import MasterBocCreate, MasterBocResponse, StageInspectionResponse, \
    StageInspectionCreate, QualityInspectionResponse, DetailedQualityInspectionResponse
from ....crud.quality_crud import MasterBocCRUD, StageInspectionCRUD, QualityInspectionCRUD

router = APIRouter(prefix="/api/v1/quality", tags=["quality"])

@router.post(
    "/master-boc/",
    response_model=MasterBocResponse,
    status_code=201
)
async def create_master_boc(
    data: MasterBocCreate,
    current_user = Depends(get_current_user)
) -> Any:
    """Create a new Master BOC entry"""
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
    "/master-boc/{id}",
    response_model=MasterBocResponse
)
async def get_master_boc(
    id: int = Path(..., gt=0),
    current_user = Depends(get_current_user)
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
    op_no: int = Query(..., gt=0),  # Add op_no as a query parameter
    current_user = Depends(get_current_user)
) -> Any:
    """Get all Master BOCs for an order and operation number"""
    try:
        master_bocs = MasterBocCRUD.get_by_order_and_op_no(order_id, op_no)
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
    current_user = Depends(get_current_user)
) -> Any:
    """Create a new Stage Inspection entry"""
    try:
        stage_inspection = StageInspectionCRUD.create_stage_inspection(data)
        return stage_inspection
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Error creating Stage Inspection: {str(e)}"
        )

@router.get(
    "/inspection/{order_id}",
    response_model=QualityInspectionResponse
)
async def get_quality_inspection_data(
    order_id: int = Path(..., gt=0),
    current_user = Depends(get_current_user)
) -> Any:
    """
    Get comprehensive quality inspection data for an order including:
    - Order information (production order, part number)
    - All stage inspections for the order with operation details
    """
    try:
        inspection_data = QualityInspectionCRUD.get_quality_inspection_data(order_id)
        return inspection_data
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Error retrieving quality inspection data: {str(e)}"
        )

@router.get(
    "/inspection/{order_id}/detailed",
    response_model=DetailedQualityInspectionResponse
)
async def get_detailed_quality_inspection(
    order_id: int = Path(..., gt=0),
    current_user = Depends(get_current_user)
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


