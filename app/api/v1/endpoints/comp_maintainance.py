from fastapi import APIRouter, HTTPException, Query
from pony.orm import db_session, select
from app.schemas.comp_maintainance import (
    MachineStatusResponse, MachineStatusOut, UpdateMachineStatusRequest,
    StatusOut, StatusResponse, RawMaterialsListResponse, RawMaterialResponse, OrderInfo
)
from app.models import MachineStatus, Status, RawMaterial

router = APIRouter(prefix="/api/v1/maintainance", tags=["maintainance"])

@router.get("/machine-status/", response_model=MachineStatusResponse)
async def get_machine_status():
    """
    Get status information for all machines.
    Returns machine make, status name, and available from date.
    Results are sorted by machine ID.
    """
    try:
        with db_session:
            machine_statuses_raw = list(select(ms for ms in MachineStatus).order_by(lambda ms: ms.machine.id))

            machine_statuses = []
            for ms in machine_statuses_raw:
                machine_status = MachineStatusOut(
                    machine_make=ms.machine.make,
                    status_name=ms.status.name,
                    available_from=ms.available_from
                )
                machine_statuses.append(machine_status)

            return MachineStatusResponse(
                total_machines=len(machine_statuses),
                statuses=machine_statuses
            )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching machine status: {str(e)}"
        )


@router.put("/machine-status/{machine_id}", response_model=MachineStatusOut)
async def update_machine_status(machine_id: int, status_update: UpdateMachineStatusRequest):
    """
    Update the status of a specific machine.
    """
    try:
        with db_session:
            # Find the existing machine status
            machine_status = MachineStatus.get(machine=machine_id)
            if not machine_status:
                raise HTTPException(
                    status_code=404,
                    detail=f"Machine status not found for machine ID: {machine_id}"
                )

            # Find the new status
            new_status = Status.get(id=status_update.status_id)
            if not new_status:
                raise HTTPException(
                    status_code=404,
                    detail=f"Status with ID {status_update.status_id} not found"
                )

            # Update the machine status
            machine_status.status = new_status
            if status_update.available_from is not None:
                machine_status.available_from = status_update.available_from

            # Create response object with updated data
            updated_status = MachineStatusOut(
                machine_make=machine_status.machine.make,
                status_name=new_status.name,
                available_from=machine_status.available_from
            )

            return updated_status

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating machine status: {str(e)}"
        )

@router.get("/status-table", response_model=StatusResponse)
async def get_all_statuses():
    """
    Get all statuses from the Status table.
    Returns a list of all status types with their descriptions.
    """
    try:
        with db_session:
            # Get all statuses ordered by id
            status_list = list(select(s for s in Status).order_by(lambda s: s.id))

            statuses = []
            for status in status_list:
                status_data = StatusOut(
                    id=status.id,
                    name=status.name,
                    description=status.description
                )
                statuses.append(status_data)

            return StatusResponse(
                total_statuses=len(statuses),
                statuses=statuses
            )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching statuses: {str(e)}"
        )


@router.get("/raw-materials/", response_model=RawMaterialsListResponse)
async def get_raw_materials():
    """
    Get raw materials with status 1 (on) or 2 (off) and their associated orders.
    Returns detailed information about raw materials including related order details.
    """
    try:
        with db_session:
            # Query raw materials with status 1 or 2
            raw_materials_query = select(rm for rm in RawMaterial
                                         if rm.status.id in [1, 2])

            raw_materials_list = []

            for rm in raw_materials_query:
                # Get orders associated with this raw material
                orders_info = []
                for order in rm.orders:
                    order_data = OrderInfo(
                        production_order=order.production_order,
                        part_number=order.part_number
                    )
                    orders_info.append(order_data)

                # Create raw material response object
                raw_material_data = RawMaterialResponse(
                    id=rm.id,
                    child_part_number=rm.child_part_number,
                    description=rm.description,
                    quantity=float(rm.quantity),  # Convert Decimal to float for JSON
                    unit_name=rm.unit.name,
                    status_name=rm.status.name,
                    available_from=rm.available_from,
                    orders=orders_info
                )
                raw_materials_list.append(raw_material_data)

            # Create final response
            response = RawMaterialsListResponse(
                total_items=len(raw_materials_list),
                raw_materials=raw_materials_list
            )

            return response

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching raw materials: {str(e)}"
        )


