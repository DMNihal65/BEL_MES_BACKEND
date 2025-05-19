from fastapi import APIRouter, HTTPException, Body, Query, Depends, BackgroundTasks
from pony.orm import db_session, select, commit, Database, Required, Optional as PonyOptional, PrimaryKey, Set, desc
from app.schemas.comp_maintainance import (
    MachineStatusResponse, MachineStatusOut, UpdateMachineStatusRequest,
    StatusOut, StatusResponse, UpdateRawMaterialRequest,
    RawMaterialResponse, OrderInfo, RawMaterialsListResponse, ReferenceDataResponse, UnitResponse, StatusResponse1,
    RawMaterialNotificationsResponse, RawMaterialNotification, MachineNotificationsResponse, MachineNotification,
    OperatorMachineUpdate, OperatorRawMaterialUpdate
)
from app.models import MachineStatus, Status, Machine, RawMaterial, InventoryStatus, Order, Unit
from app.models.logs import MachineStatusLog, RawMaterialStatusLog  # Import the new log models
from typing import Optional, Dict, List
from datetime import datetime, timedelta
from .notification_service import send_notification

router = APIRouter(prefix="/api/v1/maintainance", tags=["maintainance"])

# Function to asynchronously send notifications
async def send_machine_notification(machine_id, machine_make, status_name, description, created_by):
    """Send a machine notification with direct parameters instead of database entity"""
    try:
        with db_session:
            # Use timestamp to get the most recent log entry
            current_time = datetime.now()

            # Query the latest log for this machine using select() and order by timestamp
            latest_logs = select(
                log for log in MachineStatusLog
                if log.machine_id == machine_id
                and log.machine_make == machine_make
                and log.status_name == status_name
                and log.description == description
                and log.created_by == created_by
            ).order_by(lambda log: desc(log.updated_at)).limit(1)

            log_entries = list(latest_logs)

            # Check if we found any matching log
            if log_entries:
                log_entry = log_entries[0]
                # Pass the found log entry to notification service
                await send_notification(log_entry, "machine")
            else:
                print(f"Error: Could not find newly created machine log entry for machine_id={machine_id}")
    except Exception as e:
        print(f"Error in send_machine_notification: {str(e)}")

async def send_material_notification(material_id, part_number, status_name, description, created_by):
    """Send a material notification with direct parameters instead of database entity"""
    try:
        with db_session:
            # Build the base query
            query = select(
                log for log in RawMaterialStatusLog
                if log.material_id == material_id
                and log.status_name == status_name
                and log.description == description
                and log.created_by == created_by
            )

            # Add part_number check only if it's provided
            if part_number:
                query = query.filter(lambda log: log.part_number == part_number)

            # Order by most recent and limit to 1
            latest_logs = query.order_by(lambda log: desc(log.updated_at)).limit(1)

            log_entries = list(latest_logs)

            # Check if we found any matching log
            if log_entries:
                log_entry = log_entries[0]
                # Pass the found log entry to notification service
                await send_notification(log_entry, "material")
            else:
                print(f"Error: Could not find newly created material log entry for material_id={material_id}")
    except Exception as e:
        print(f"Error in send_material_notification: {str(e)}")

# Updated endpoint for operators to send machine status updates to supervisors
@router.post("/operator/machine-update/{machine_id}", response_model=MachineStatusOut)
async def operator_machine_update(machine_id: int, update: OperatorMachineUpdate, background_tasks: BackgroundTasks):
    """
    Endpoint for operators to send machine status updates to supervisors.
    Allows operators to turn machine on/off and provide a description.
    Creates supervisor notifications with persistent storage in logs schema.
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

            # Create log entry in the logs schema
            log_entry = MachineStatusLog(
                machine_id=machine_id,
                machine_make=machine.make,
                status_name=new_status.name,
                description=update.description,
                updated_at=current_time,
                created_by=update.created_by,
                is_acknowledged=False
            )
            commit()  # Ensure the transaction is committed

            # Store values we need for notification
            machine_make = machine.make
            status_name = new_status.name
            description = update.description
            created_by = update.created_by

            # Add task to send notification asynchronously
            background_tasks.add_task(
                send_machine_notification,
                machine_id,
                machine_make,
                status_name,
                description,
                created_by
            )

            # Create response object with updated data
            updated_status = MachineStatusOut(
                machine_make=machine.make,
                machine_id= machine.id,
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
async def operator_raw_material_update(part_number: str, update: OperatorRawMaterialUpdate, background_tasks: BackgroundTasks):
    """
    Endpoint for operators to send raw material status updates to supervisors.
    Allows operators to mark raw materials as available/unavailable and provide a description.
    Creates supervisor notifications with persistent storage in logs schema.
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

            # Create log entry in the logs schema
            log_entry = RawMaterialStatusLog(
                material_id=raw_material.id,
                part_number=notification_part_number,
                status_name=new_status.name,
                description=update.description,
                updated_at=current_time,
                created_by=update.created_by,
                is_acknowledged=False
            )
            commit()  # Ensure the transaction is committed

            # Store values we need for notification
            material_id = raw_material.id
            status_name = new_status.name
            description = update.description
            created_by = update.created_by

            # Add task to send notification asynchronously
            background_tasks.add_task(
                send_material_notification,
                material_id,
                notification_part_number,
                status_name,
                description,
                created_by
            )

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


# Updated endpoint for machine notifications - using logs schema
@router.get("/supervisor/machine-notifications/", response_model=MachineNotificationsResponse)
async def get_supervisor_machine_notifications(
        hours: Optional[int] = Query(None, description="Get notifications from the last X hours"),
        status: Optional[str] = Query(None, description="Filter by status name (e.g., 'stopped', 'running')"),
        machine_id: Optional[int] = Query(None, description="Filter by machine ID"),
        limit: Optional[int] = Query(None, description="Limit the number of results"),
        acknowledged: Optional[bool] = Query(None, description="Filter by acknowledgment status")
):
    """
    Get machine status notifications for supervisors from logs schema.
    Returns all notifications sent by operators with filtering options.
    """
    try:
        with db_session:
            # Start with a base query
            query = select(log for log in MachineStatusLog)

            # Apply time filter if specified
            if hours:
                time_threshold = datetime.now() - timedelta(hours=hours)
                query = query.filter(lambda log: log.updated_at >= time_threshold)

            # Apply status filter if specified
            if status:
                status_lower = status.lower()
                query = query.filter(lambda log: status_lower in log.status_name.lower())

            # Apply machine_id filter if specified
            if machine_id:
                query = query.filter(lambda log: log.machine_id == machine_id)

            # Apply acknowledgment filter if specified
            if acknowledged is not None:
                query = query.filter(lambda log: log.is_acknowledged == acknowledged)

            # Order by timestamp, newest first
            query = query.order_by(lambda log: desc(log.updated_at))

            # Apply limit if specified
            if limit:
                query = query.limit(limit)

            # Execute query and convert to notification objects
            log_entities = list(query)
            notifications = []

            for entity in log_entities:
                # Create notification with explicit ID
                notification = MachineNotification(
                    id=entity.id,  # Explicitly include notification ID
                    machine_id=entity.machine_id,
                    machine_make=entity.machine_make,
                    status_name=entity.status_name,
                    description=entity.description,
                    updated_at=entity.updated_at,
                    created_by=entity.created_by,
                    is_acknowledged=entity.is_acknowledged,
                    acknowledged_by=entity.acknowledged_by,
                    acknowledged_at=entity.acknowledged_at
                )
                notifications.append(notification)

            return MachineNotificationsResponse(
                total_notifications=len(notifications),
                notifications=notifications
            )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching machine notifications: {str(e)}"
        )


# Updated endpoint for raw material notifications - using logs schema
@router.get("/supervisor/raw-material-notifications/", response_model=RawMaterialNotificationsResponse)
async def get_supervisor_raw_material_notifications(
        hours: Optional[int] = Query(None, description="Get notifications from the last X hours"),
        status: Optional[str] = Query(None, description="Filter by status name (e.g., 'unavailable', 'available')"),
        material_id: Optional[int] = Query(None, description="Filter by raw material ID"),
        part_number: Optional[str] = Query(None, description="Filter by part number"),
        limit: Optional[int] = Query(None, description="Limit the number of results"),
        acknowledged: Optional[bool] = Query(None, description="Filter by acknowledgment status")
):
    """
    Get raw material status notifications for supervisors from logs schema.
    Returns all notifications sent by operators with filtering options.
    """
    try:
        with db_session:
            # Start with a base query
            query = select(log for log in RawMaterialStatusLog)

            # Apply time filter if specified
            if hours:
                time_threshold = datetime.now() - timedelta(hours=hours)
                query = query.filter(lambda log: log.updated_at >= time_threshold)

            # Apply status filter if specified
            if status:
                status_lower = status.lower()
                query = query.filter(lambda log: status_lower in log.status_name.lower())

            # Apply material_id filter if specified
            if material_id:
                query = query.filter(lambda log: log.material_id == material_id)

            # Apply part_number filter if specified
            if part_number and part_number.strip():
                part_number_lower = part_number.lower()
                query = query.filter(lambda log: log.part_number and part_number_lower in log.part_number.lower())

            # Apply acknowledgment filter if specified
            if acknowledged is not None:
                query = query.filter(lambda log: log.is_acknowledged == acknowledged)

            # Order by timestamp, newest first
            query = query.order_by(lambda log: desc(log.updated_at))

            # Apply limit if specified
            if limit:
                query = query.limit(limit)

            # Execute query and convert to notification objects
            log_entities = list(query)
            notifications = []

            for entity in log_entities:
                # Create notification with explicit ID
                notification = RawMaterialNotification(
                    id=entity.id,  # Explicitly include notification ID
                    material_id=entity.material_id,
                    part_number=entity.part_number,
                    status_name=entity.status_name,
                    description=entity.description,
                    updated_at=entity.updated_at,
                    created_by=entity.created_by,
                    is_acknowledged=entity.is_acknowledged,
                    acknowledged_by=entity.acknowledged_by,
                    acknowledged_at=entity.acknowledged_at
                )
                notifications.append(notification)

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
        limit: Optional[int] = Query(None, description="Limit the number of results"),
        acknowledged: Optional[bool] = Query(None, description="Filter by acknowledgment status")
):
    """
    Get machine status updates with persistent database storage.
    Returns all updates with timestamps from the database.
    """
    # This endpoint uses the same implementation as the notifications endpoint
    # since we're now storing all updates in the database
    return await get_supervisor_machine_notifications(hours, status, machine_id, limit, acknowledged)


# Updated endpoint for administrators/supervisors to update machine status
class MachineNotificationEntity:
    pass


# @router.put("/machine-status/{machine_id}", response_model=MachineStatusOut)
# async def update_machine_status(machine_id: int, status_update: UpdateMachineStatusRequest):
#     """
#     Update the status of a specific machine and persist the change to the notification database.
#     """
#     try:
#         with db_session:
#             # Find the existing machine status
#             machine_status = MachineStatus.get(machine=machine_id)
#             if not machine_status:
#                 raise HTTPException(
#                     status_code=404,
#                     detail=f"Machine status not found for machine ID: {machine_id}"
#                 )
#
#             # Find the new status
#             new_status = Status.get(id=status_update.status_id)
#             if not new_status:
#                 raise HTTPException(
#                     status_code=404,
#                     detail=f"Status with ID {status_update.status_id} not found"
#                 )
#
#             # Update the machine status
#             machine_status.status = new_status
#             if status_update.available_from is not None:
#                 machine_status.available_from = status_update.available_from
#
#             # Update description
#             machine_status.description = status_update.description
#
#             # Create response object with updated data
#             updated_status = MachineStatusOut(
#                 machine_make=machine_status.machine.make,
#                 status_name=new_status.name,
#                 available_from=machine_status.available_from,
#                 description=machine_status.description
#             )
#
#             # Also add to the notification database when supervisor updates
#             current_time = datetime.now()
#
#             # Create notification in persistent database
#             with db_session:
#                 MachineNotificationEntity(
#                     machine_id=machine_id,
#                     machine_make=machine_status.machine.make,
#                     status_name=new_status.name,
#                     description=status_update.description,
#                     updated_at=current_time
#                 )
#                 commit()  # Ensure the transaction is committed
#
#             return updated_status
#
#     except HTTPException as he:
#         raise he
#     except Exception as e:
#         raise HTTPException(
#             status_code=500,
#             detail=f"Error updating machine status: {str(e)}"
#         )


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
                    machine_id = ms.machine.id,
                    status_name=ms.status.name,
                    available_from=ms.available_from,
                    available_to=ms.available_to,
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


@router.put("/machine-status/{machine_id}", response_model=MachineStatusOut)
async def update_machine_status(machine_id: int, status_update: UpdateMachineStatusRequest):
    """
    Update the status of a specific machine.
    Can update status, description, available_from, and available_to date.
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

            # Update date range if provided
            if status_update.available_from is not None:
                machine_status.available_from = status_update.available_from

            # Update available_to date if provided
            if status_update.available_to is not None:
                machine_status.available_to = status_update.available_to

            # Validate the date range if both dates are provided
            if machine_status.available_from and machine_status.available_to:
                if machine_status.available_from > machine_status.available_to:
                    raise HTTPException(
                        status_code=400,
                        detail="available_from date cannot be after available_to date"
                    )

            # Update description
            machine_status.description = status_update.description

            # Create response object with updated data
            updated_status = MachineStatusOut(
                machine_make=machine_status.machine.make,
                machine_id=machine_status.machine.id,
                status_name=new_status.name,
                available_from=machine_status.available_from,
                available_to=machine_status.available_to,  # Added available_to
                description=machine_status.description
            )

            return updated_status

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating machine status: {str(e)}"
        )