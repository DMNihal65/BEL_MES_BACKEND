from fastapi import APIRouter, HTTPException, Body, Query, Depends
from pony.orm import db_session, select, commit, Database, Required, Optional as PonyOptional, PrimaryKey, Set, desc
from app.schemas.comp_maintainance import (
    MachineStatusResponse, MachineStatusOut, UpdateMachineStatusRequest,
    StatusOut, StatusResponse, UpdateRawMaterialRequest,
    RawMaterialResponse, OrderInfo, RawMaterialsListResponse, ReferenceDataResponse, UnitResponse, StatusResponse1,
    RawMaterialNotificationsResponse, RawMaterialNotification, MachineNotificationsResponse, MachineNotification
)
from app.models import MachineStatus, Status, Machine, RawMaterial, InventoryStatus, Order, Unit
from typing import Optional, Dict, List
from datetime import datetime, timedelta
from pydantic import BaseModel
import os

router = APIRouter(prefix="/api/v1/maintainance", tags=["maintainance"])

# Create a separate database for notifications to ensure persistence
# Use SQLite for simplicity and reliability
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notifications.sqlite")
notification_db = Database()


# Define database entities for persistent storage
class MachineNotificationEntity(notification_db.Entity):
    _table_ = "machine_notifications"
    id = PrimaryKey(int, auto=True)
    machine_id = Required(int)
    machine_make = Required(str)
    status_name = Required(str)
    description = PonyOptional(str)
    updated_at = Required(datetime)

    def to_dict(self):
        return {
            "machine_id": self.machine_id,
            "machine_make": self.machine_make,
            "status_name": self.status_name,
            "description": self.description,
            "updated_at": self.updated_at
        }


class RawMaterialNotificationEntity(notification_db.Entity):
    _table_ = "raw_material_notifications"
    id = PrimaryKey(int, auto=True)
    material_id = Required(int)
    part_number = PonyOptional(str)
    status_name = Required(str)
    description = PonyOptional(str)
    updated_at = Required(datetime)

    def to_dict(self):
        return {
            "id": self.material_id,
            "part_number": self.part_number,
            "status_name": self.status_name,
            "description": self.description,
            "updated_at": self.updated_at
        }


# Connect to SQLite database
notification_db.bind(provider='sqlite', filename=DB_PATH, create_db=True)
notification_db.generate_mapping(create_tables=True)


# New Pydantic models for the operator to send updates
class OperatorMachineUpdate(BaseModel):
    description: str
    is_on: bool  # True for machine ON, False for machine OFF


class OperatorRawMaterialUpdate(BaseModel):
    description: str
    is_available: bool  # True for available, False for unavailable


# Updated endpoint for operators to send machine status updates to supervisors
@router.post("/operator/machine-update/{machine_id}", response_model=MachineStatusOut)
async def operator_machine_update(machine_id: int, update: OperatorMachineUpdate):
    """
    Endpoint for operators to send machine status updates to supervisors.
    Allows operators to turn machine on/off and provide a description.
    Creates supervisor notifications with database persistence.
    """
    try:
        with db_session:
            # Find the machine by ID
            machine = Machine.get(id=machine_id)
            if not machine:
                raise HTTPException(
                    status_code=404,
                    detail=f"Machine with ID {machine_id} not found"
                )

            # Get the latest machine status record
            machine_status = MachineStatus.get(machine=machine_id)
            if not machine_status:
                raise HTTPException(
                    status_code=404,
                    detail=f"Machine status not found for machine ID: {machine_id}"
                )

            # First, let's get all available statuses to find the best match
            all_statuses = list(select(s for s in Status))

            # Map common status terms to potential matches in the database
            running_terms = ["running", "active", "on", "operational", "working"]
            stopped_terms = ["stopped", "inactive", "off", "non-operational", "down", "standby"]

            desired_status_type = running_terms if update.is_on else stopped_terms

            # Try to find a matching status
            new_status = None
            for status in all_statuses:
                status_lower = status.name.lower()
                if any(term in status_lower for term in desired_status_type):
                    new_status = status
                    break

            # If no matching status, use the first status or create a new one
            if not new_status and all_statuses:
                # Fallback to the first status in the database
                new_status = all_statuses[0]
                # Log this for debugging
                print(
                    f"WARNING: No matching status found for {'Running' if update.is_on else 'Stopped'}. Using {new_status.name} as fallback.")

            # If still no status, create a new one (optional)
            if not new_status:
                status_name = "Running" if update.is_on else "Stopped"
                # Create a new status if none exists
                new_status = Status(
                    name=status_name,
                    description=f"{'Machine is operational' if update.is_on else 'Machine is not operational'}"
                )
                # Flush to get the ID
                commit()

            # For the response, set the current time
            current_time = datetime.now()

            # Create notification in persistent database
            with db_session:
                MachineNotificationEntity(
                    machine_id=machine_id,
                    machine_make=machine.make,
                    status_name=new_status.name,
                    description=update.description,
                    updated_at=current_time
                )
                commit()  # Ensure the transaction is committed

            # Create response object with updated data
            updated_status = MachineStatusOut(
                machine_make=machine.make,
                status_name=new_status.name,
                available_from=current_time,  # Ensure this is not null
                description=update.description
            )

            return updated_status

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating machine status: {str(e)}"
        )


# Updated endpoint for operators to send raw material status updates to supervisors
@router.post("/operator/raw-material-update/{part_number}", response_model=RawMaterialResponse)
async def operator_raw_material_update(part_number: str, update: OperatorRawMaterialUpdate):
    """
    Endpoint for operators to send raw material status updates to supervisors.
    Allows operators to mark raw materials as available/unavailable and provide a description.
    Creates supervisor notifications with database persistence.
    """
    try:
        with db_session:
            # First try to find raw material by part_number in orders
            raw_material = select(rm for rm in RawMaterial
                                  for o in rm.orders
                                  if o.part_number == part_number).first()

            # If not found, try to find by child_part_number
            if not raw_material:
                raw_material = RawMaterial.get(child_part_number=part_number)

            if not raw_material:
                raise HTTPException(
                    status_code=404,
                    detail=f"Raw material with part number {part_number} not found"
                )

            # Get all available inventory statuses to find the best match
            all_statuses = list(select(s for s in InventoryStatus))

            # Map common status terms to potential matches in the database
            available_terms = ["available", "in stock", "ready", "accessible"]
            unavailable_terms = ["unavailable", "out of stock", "not ready", "inaccessible"]

            desired_status_type = available_terms if update.is_available else unavailable_terms

            # Try to find a matching status
            new_status = None
            for status in all_statuses:
                status_lower = status.name.lower()
                if any(term in status_lower for term in desired_status_type):
                    new_status = status
                    break

            # If no matching status, use the first status or create a new one
            if not new_status and all_statuses:
                # Fallback to the first status in the database
                new_status = all_statuses[0]
                # Log this for debugging
                print(
                    f"WARNING: No matching status found for {'Available' if update.is_available else 'Unavailable'}. Using {new_status.name} as fallback.")

            # If still no status, create a new one (optional)
            if not new_status:
                status_name = "Available" if update.is_available else "Unavailable"
                # Create a new status if none exists
                new_status = InventoryStatus(
                    name=status_name,
                    description=f"{'Material is available for use' if update.is_available else 'Material is not available for use'}"
                )
                # Flush to get the ID
                commit()

            # For the response, set the current time
            current_time = datetime.now()

            # Get part number from first order if available for notification
            notification_part_number = None
            if raw_material.orders:
                first_order = list(raw_material.orders)[0]
                notification_part_number = first_order.part_number

            # Create notification in persistent database
            with db_session:
                RawMaterialNotificationEntity(
                    material_id=raw_material.id,
                    part_number=notification_part_number,
                    status_name=new_status.name,
                    description=update.description,
                    updated_at=current_time
                )
                commit()  # Ensure the transaction is committed

            # Create orders list for response
            orders_info = [
                OrderInfo(
                    production_order=order.production_order,
                    part_number=order.part_number
                ) for order in raw_material.orders
            ]

            # Create response object with updated data
            updated_material = RawMaterialResponse(
                id=raw_material.id,
                child_part_number=raw_material.child_part_number,
                description=update.description,  # Use the new description
                quantity=float(raw_material.quantity),
                unit_name=raw_material.unit.name,
                status_name=new_status.name,
                available_from=current_time if not update.is_available else None,
                orders=orders_info
            )

            return updated_material

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating raw material status: {str(e)}"
        )


# Updated endpoint for machine notifications - with database persistence
@router.get("/supervisor/machine-notifications/", response_model=MachineNotificationsResponse)
async def get_supervisor_machine_notifications(
        hours: Optional[int] = Query(None, description="Get notifications from the last X hours"),
        status: Optional[str] = Query(None, description="Filter by status name (e.g., 'stopped', 'running')"),
        machine_id: Optional[int] = Query(None, description="Filter by machine ID"),
        limit: Optional[int] = Query(None, description="Limit the number of results")
):
    """
    Get machine status notifications for supervisors with database persistence.
    Returns all notifications sent by operators from the persistent database, with filtering options.
    """
    try:
        with db_session:
            # Start with a base query
            query = select(n for n in MachineNotificationEntity)

            # Apply time filter if specified
            if hours:
                time_threshold = datetime.now() - timedelta(hours=hours)
                query = query.filter(lambda n: n.updated_at >= time_threshold)

            # Apply status filter if specified
            if status:
                status_lower = status.lower()
                query = query.filter(lambda n: status_lower in n.status_name.lower())

            # Apply machine_id filter if specified
            if machine_id:
                query = query.filter(lambda n: n.machine_id == machine_id)

            # Order by timestamp, newest first
            query = query.order_by(lambda n: desc(n.updated_at))

            # Apply limit if specified
            if limit:
                query = query.limit(limit)

            # Execute query and convert to notification objects
            notification_entities = list(query)
            notifications = [
                MachineNotification(**entity.to_dict())
                for entity in notification_entities
            ]

            return MachineNotificationsResponse(
                total_notifications=len(notifications),
                notifications=notifications
            )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching machine notifications: {str(e)}"
        )


# Updated endpoint for raw material notifications - with database persistence
@router.get("/supervisor/raw-material-notifications/", response_model=RawMaterialNotificationsResponse)
async def get_supervisor_raw_material_notifications(
        hours: Optional[int] = Query(None, description="Get notifications from the last X hours"),
        status: Optional[str] = Query(None, description="Filter by status name (e.g., 'unavailable', 'available')"),
        material_id: Optional[int] = Query(None, description="Filter by raw material ID"),
        part_number: Optional[str] = Query(None, description="Filter by part number"),
        limit: Optional[int] = Query(None, description="Limit the number of results")
):
    """
    Get raw material status notifications for supervisors with database persistence.
    Returns all notifications sent by operators from the persistent database, with filtering options.
    """
    try:
        with db_session:
            # Start with a base query
            query = select(n for n in RawMaterialNotificationEntity)

            # Apply time filter if specified
            if hours:
                time_threshold = datetime.now() - timedelta(hours=hours)
                query = query.filter(lambda n: n.updated_at >= time_threshold)

            # Apply status filter if specified
            if status:
                status_lower = status.lower()
                query = query.filter(lambda n: status_lower in n.status_name.lower())

            # Apply material_id filter if specified
            if material_id:
                query = query.filter(lambda n: n.material_id == material_id)

            # Apply part_number filter if specified
            if part_number and part_number.strip():
                part_number_lower = part_number.lower()
                query = query.filter(lambda n: n.part_number and part_number_lower in n.part_number.lower())

            # Order by timestamp, newest first
            query = query.order_by(lambda n: desc(n.updated_at))

            # Apply limit if specified
            if limit:
                query = query.limit(limit)

            # Execute query and convert to notification objects
            notification_entities = list(query)
            notifications = [
                RawMaterialNotification(**entity.to_dict())
                for entity in notification_entities
            ]

            return RawMaterialNotificationsResponse(
                total_notifications=len(notifications),
                notifications=notifications
            )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching raw material notifications: {str(e)}"
        )


# Updated endpoint for machine updates - with database persistence
@router.get("/supervisor/machine-updates/", response_model=MachineNotificationsResponse)
async def get_supervisor_machine_updates(
        hours: Optional[int] = Query(None, description="Get updates from the last X hours"),
        status: Optional[str] = Query(None, description="Filter by status name"),
        machine_id: Optional[int] = Query(None, description="Filter by machine ID"),
        limit: Optional[int] = Query(None, description="Limit the number of results")
):
    """
    Get machine status updates with persistent database storage.
    Returns all updates with timestamps from the database.
    """
    # This endpoint uses the same implementation as the notifications endpoint
    # since we're now storing all updates in the database
    return await get_supervisor_machine_notifications(hours, status, machine_id, limit)


# Updated endpoint for administrators/supervisors to update machine status
@router.put("/machine-status/{machine_id}", response_model=MachineStatusOut)
async def update_machine_status(machine_id: int, status_update: UpdateMachineStatusRequest):
    """
    Update the status of a specific machine and persist the change to the notification database.
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

            # Update description
            machine_status.description = status_update.description

            # Create response object with updated data
            updated_status = MachineStatusOut(
                machine_make=machine_status.machine.make,
                status_name=new_status.name,
                available_from=machine_status.available_from,
                description=machine_status.description
            )

            # Also add to the notification database when supervisor updates
            current_time = datetime.now()

            # Create notification in persistent database
            with db_session:
                MachineNotificationEntity(
                    machine_id=machine_id,
                    machine_make=machine_status.machine.make,
                    status_name=new_status.name,
                    description=status_update.description,
                    updated_at=current_time
                )
                commit()  # Ensure the transaction is committed

            return updated_status

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating machine status: {str(e)}"
        )