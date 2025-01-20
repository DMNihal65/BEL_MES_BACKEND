# app/crud/raw_material.py

from pony.orm import db_session, select
from app.models import RawMaterial, Machine, InventoryStatus, MachineStatus
from app.schemas.raw_material import RawMaterialIn, RawMaterialOut, MachineStatusIn, MachineStatusOut
from typing import List, Optional
from datetime import datetime


@db_session
def fetch_machine_statuses():
    """
    Fetch machine statuses - Updated to handle Set relationship
    """
    machine_status_query = select((m, ms) for m in Machine
                                  for ms in m.status)[:]  # Using the status Set relationship
    return [
        MachineStatusOut(
            machine_id=m.id,
            machine_name=m.make,
            status=ms.description,  # Using description instead of name
            available_from=m.available_from
        )
        for m, ms in machine_status_query
    ]


@db_session
def update_machine_status(machine_id: int, status_id: int,
                          available_from: Optional[datetime] = None) -> MachineStatusOut:
    """
    Update machine status and availability
    """
    machine = Machine[machine_id]
    machine_status = MachineStatus[status_id]

    # Clear existing statuses and add new one
    machine.status.clear()
    machine.status.add(machine_status)

    if available_from:
        machine.available_from = available_from

    return MachineStatusOut(
        machine_id=machine.id,
        machine_name=machine.make,
        status=machine_status.description,  # Using description instead of name
        available_from=machine.available_from
    )


@db_session
def fetch_raw_materials():
    """
    Fetch raw materials with their current InventoryStatus
    """
    raw_materials_query = select((rm, ist) for rm in RawMaterial
                                 for ist in InventoryStatus if rm.status == ist)[:]
    return [
        RawMaterialOut(
            id=rm.id,
            child_part_number=rm.child_part_number,
            quantity=rm.quantity,
            unit=rm.unit,
            status=ist.name,
            available_from=rm.available_from
        )
        for rm, ist in raw_materials_query
    ]


@db_session
def update_raw_material_status(raw_material_id: int, status_id: int,
                               available_from: Optional[datetime] = None) -> RawMaterialOut:
    """
    Update raw material status and availability
    """
    rm = RawMaterial[raw_material_id]
    inventory_status = InventoryStatus[status_id]
    rm.status = inventory_status
    if available_from:
        rm.available_from = available_from

    return RawMaterialOut(
        id=rm.id,
        child_part_number=rm.child_part_number,
        quantity=rm.quantity,
        unit=rm.unit,
        status=inventory_status.name,
        available_from=rm.available_from
    )