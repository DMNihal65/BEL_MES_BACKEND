from fastapi import APIRouter, HTTPException, Query
from pony.orm import db_session, select
from app.schemas.comp_maintainance import (
    MachineStatusResponse, MachineStatusOut, UpdateMachineStatusRequest,
    StatusOut, StatusResponse, UpdateRawMaterialRequest,
    RawMaterialResponse, OrderInfo, RawMaterialsListResponse, ReferenceDataResponse, UnitResponse, StatusResponse1
)
from app.models import MachineStatus, Status, RawMaterial, InventoryStatus, Order, Unit

router = APIRouter(prefix="/api/v1/maintainance", tags=["maintainance"])

@router.get("/machine-status/", response_model=MachineStatusResponse)
async def get_machine_status():
    """
    Get status information for all machines.
    Returns machine make, status name, description, and available from date.
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
                    available_from=ms.available_from,
                    description=ms.description  # Added description
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

            # Update description - add this line
            machine_status.description = status_update.description

            # Create response object with updated data
            updated_status = MachineStatusOut(
                machine_make=machine_status.machine.make,
                status_name=new_status.name,
                available_from=machine_status.available_from,
                description=machine_status.description  # Add this line
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
    Get raw materials with status 'available' or 'unavailable' and their associated orders.
    Returns detailed information about raw materials including related order details.
    """
    try:
        with db_session:
            # First get the status IDs for 'available' and 'unavailable'
            status_query = select(s for s in InventoryStatus
                                  if s.name.lower() in ['available', 'unavailable'])
            status_ids = [s.id for s in status_query]

            # Query raw materials with the found status IDs
            raw_materials_query = select(rm for rm in RawMaterial
                                         if rm.status.id in status_ids)

            raw_materials_list = []

            for rm in raw_materials_query:
                orders_info = []
                for order in rm.orders:
                    order_data = OrderInfo(
                        production_order=order.production_order,
                        part_number=order.part_number
                    )
                    orders_info.append(order_data)

                raw_material_data = RawMaterialResponse(
                    id=rm.id,
                    child_part_number=rm.child_part_number,
                    description=rm.description,
                    quantity=float(rm.quantity),
                    unit_name=rm.unit.name,
                    status_name=rm.status.name,
                    available_from=rm.available_from,
                    orders=orders_info
                )
                raw_materials_list.append(raw_material_data)

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


@router.put("/raw-materials/{part_number}", response_model=RawMaterialResponse)
async def update_raw_material(part_number: str, update_data: UpdateRawMaterialRequest):
    """
    Update raw material details based on part number.
    Note: child_part_number and part_number cannot be modified.
    """
    try:
        with db_session:
            # First try to find raw material by part_number in orders
            raw_material_query = select(rm for rm in RawMaterial
                                        for o in rm.orders
                                        if o.part_number == part_number).first()

            # If not found, try to find by child_part_number
            if not raw_material_query:
                raw_material_query = RawMaterial.get(child_part_number=part_number)

            if not raw_material_query:
                raise HTTPException(
                    status_code=404,
                    detail=f"Raw material with part number {part_number} not found"
                )

            # Verify the unit exists
            unit = Unit.get(id=update_data.unit_id)
            if not unit:
                raise HTTPException(
                    status_code=404,
                    detail=f"Unit with ID {update_data.unit_id} not found"
                )

            # Verify the status exists
            status = InventoryStatus.get(id=update_data.status_id)
            if not status:
                raise HTTPException(
                    status_code=404,
                    detail=f"Status with ID {update_data.status_id} not found"
                )

            # Update the modifiable fields
            raw_material_query.description = update_data.description
            raw_material_query.quantity = update_data.quantity
            raw_material_query.unit = unit
            raw_material_query.status = status
            raw_material_query.available_from = update_data.available_from

            # Create response with updated data
            orders_info = [
                OrderInfo(
                    production_order=order.production_order,
                    part_number=order.part_number
                ) for order in raw_material_query.orders
            ]

            response = RawMaterialResponse(
                id=raw_material_query.id,
                child_part_number=raw_material_query.child_part_number,
                description=raw_material_query.description,
                quantity=float(raw_material_query.quantity),
                unit_name=raw_material_query.unit.name,
                status_name=raw_material_query.status.name,
                available_from=raw_material_query.available_from,
                orders=orders_info
            )

            return response

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating raw material: {str(e)}"
        )

@router.get("/status-unit-rmdata/", response_model=ReferenceDataResponse)
async def get_reference_data():
    """
    Get reference data for units and statuses from their respective tables.
    """
    try:
        with db_session:
            # Fetch statuses from inventory_status table
            statuses = list(select(s for s in InventoryStatus))
            status_data = [
                StatusResponse1(
                    id=s.id,
                    name=s.name,
                    description=s.description
                ) for s in statuses
            ]

            # Fetch units from inventory.units table
            units = list(select(u for u in Unit))
            unit_data = [
                UnitResponse(
                    id=u.id,
                    name=u.name
                ) for u in units
            ]

            return ReferenceDataResponse(
                statuses=status_data,
                units=unit_data
            )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching reference data: {str(e)}"
        )