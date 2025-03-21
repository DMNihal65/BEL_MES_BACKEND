from fastapi import APIRouter, HTTPException, Depends, Path, Query
from typing import Any, List
import os
import subprocess

from app.core.security import get_current_user
from app.schemas.quality import MasterBocCreate, MasterBocResponse, StageInspectionResponse, \
    StageInspectionCreate, QualityInspectionResponse, DetailedQualityInspectionResponse, \
    OrderIPIDResponse, MasterBocIPIDInfo, MeasurementInstrumentsResponse
from app.crud.quality import MasterBocCRUD, StageInspectionCRUD, QualityInspectionCRUD


# Add the EXE_PATH constant at the top of the file
EXE_PATH = "D:\siri\BEL\BEL_MES_BACKEND\GDNT_updates2.exe"  # Replace with your actual exe path

router = APIRouter(prefix="/quality", tags=["quality"])

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
    "/master-boc/measurement-instruments",
    response_model=MeasurementInstrumentsResponse,
    summary="Get all measurement instruments"
)
async def get_measurement_instruments(
    current_user = Depends(get_current_user)
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
    op_no: int = Query(..., gt=0),
    measurement_instruments: List[str] = Query(None, description="Filter by multiple measurement instruments"),
    current_user = Depends(get_current_user)
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

@router.get(
    "/master-boc/ipids/{order_id}",
    response_model=OrderIPIDResponse
)
async def get_order_ipids(
    order_id: int = Path(..., gt=0),
    current_user = Depends(get_current_user)
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

@router.get("/run")
async def run_exe(
    current_user = Depends(get_current_user)
) -> Any:
    """
    Run an external executable file
    """
    if not os.path.exists(EXE_PATH):
        raise HTTPException(
            status_code=404,
            detail="Executable not found"
        )

    try:
        subprocess.Popen(EXE_PATH, shell=True)
        return {"message": "Executable started successfully"}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error running executable: {str(e)}"
        )


