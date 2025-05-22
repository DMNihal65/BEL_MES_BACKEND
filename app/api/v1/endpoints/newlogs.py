from fastapi import APIRouter, HTTPException, Query, logger, Depends
from pony.orm import db_session, select, desc, get_current_user
from datetime import datetime, date, timedelta
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from app.models import User
from app.models.inventoryv1 import InventoryItem, CalibrationSchedule
from app.models.logs import MachineStatusLog, MachineCalibrationLog, InstrumentCalibrationLog, RawMaterialStatusLog
from app.schemas.comp_maintainance import RawMaterialNotificationsResponse, RawMaterialNotification, \
    MachineNotification, MachineNotificationsResponse

router = APIRouter(prefix="/api/v1/newlogs", tags=["newlogs"])





class InventoryItemData(BaseModel):
    id: int
    item_code: str
    status: str
    quantity: int
    available_quantity: int
    subcategory_id: int
    subcategory_name: Optional[str] = None
    dynamic_data: Dict[str, Any]

    class Config:
        from_attributes = True


class MachineCalibrationLogResponse(BaseModel):
    id: int
    timestamp: datetime
    calibration_due_date: Optional[date] = None
    machine_id: Optional[int] = None
    machine_details: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class InstrumentCalibrationLogResponse(BaseModel):
    id: int
    timestamp: datetime
    calibration_due_date: Optional[date] = None
    instrument_id: Optional[int] = None
    instrument_details: Optional[Dict[str, Any]] = None  # Changed to Dict to include dynamic_data

    class Config:
        from_attributes = True





@router.get("/machine-calibration-logs", response_model=List[MachineCalibrationLogResponse])
@db_session
def get_machine_calibration_logs(
        machine_id: Optional[int] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        due_before: Optional[date] = None,
        skip: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=1000),
        current_user=Depends(get_current_user)
):
    """
    Get machine calibration logs with optional filtering by machine_id,
    timestamp range, and due date.
    """
    query = select(log for log in MachineCalibrationLog)

    # Apply filters if provided
    if machine_id is not None:
        query = query.filter(lambda log: log.machine_id.id == machine_id if log.machine_id else False)

    if start_date is not None:
        query = query.filter(lambda log: log.timestamp >= start_date)

    if end_date is not None:
        query = query.filter(lambda log: log.timestamp <= end_date)

    if due_before is not None:
        query = query.filter(lambda log: log.calibration_due_date <= due_before if log.calibration_due_date else False)

    # Apply pagination
    query = query.limit(limit, offset=skip)

    # Execute query
    logs = list(query)

    if not logs:
        return []

    # Convert PonyORM entities to dictionaries with machine details
    result = []
    for log in logs:
        log_dict = {
            "id": log.id,
            "timestamp": log.timestamp,
            "calibration_due_date": log.calibration_due_date,
            "machine_id": log.machine_id.id if log.machine_id else None,
            "machine_details": log.machine_id.to_dict() if log.machine_id else None
        }
        result.append(log_dict)

    return result





@router.get("/instrument-calibration-logs", response_model=List[InstrumentCalibrationLogResponse])
@db_session
def get_instrument_calibration_logs(
        instrument_id: Optional[int] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        due_before: Optional[date] = None,
        skip: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=1000),
        current_user=Depends(get_current_user)
):
    """
    Get instrument calibration logs with optional filtering by instrument_id,
    timestamp range, and due date.
    """
    query = select(log for log in InstrumentCalibrationLog)

    # Apply filters if provided
    if instrument_id is not None:
        # First, we need to determine the correct relationship
        # If InstrumentCalibrationLog has a direct relationship to CalibrationSchedule
        # it might be named differently. Let's try common variations:

        # Option 1: If it has a calibration_schedule_id or schedule field
        try:
            query = query.filter(lambda log: log.schedule.inventory_item.id == instrument_id if hasattr(log,
                                                                                                        'schedule') and log.schedule else False)
        except:
            try:
                # Option 2: If it has a calibration_schedule_id field
                query = query.filter(lambda log: log.calibration_schedule_id and
                                                 CalibrationSchedule.get(id=log.calibration_schedule_id) and
                                                 CalibrationSchedule.get(
                                                     id=log.calibration_schedule_id).inventory_item.id == instrument_id)
            except:
                # Option 3: If we need to join through a different relationship
                # We'll filter the logs that have matching calibration schedules
                calibration_schedule_ids = select(
                    cs.id for cs in CalibrationSchedule if cs.inventory_item.id == instrument_id)
                query = query.filter(lambda log: log.calibration_schedule_id in calibration_schedule_ids if hasattr(log,
                                                                                                                    'calibration_schedule_id') else False)

    if start_date is not None:
        query = query.filter(lambda log: log.timestamp >= start_date)

    if end_date is not None:
        query = query.filter(lambda log: log.timestamp <= end_date)

    if due_before is not None:
        query = query.filter(lambda log: log.calibration_due_date <= due_before if log.calibration_due_date else False)

    # Apply pagination
    query = query.limit(limit, offset=skip)

    # Execute query
    logs = list(query)

    if not logs:
        return []

    # Convert PonyORM entities to dictionaries with instrument details from InventoryItem
    result = []
    for log in logs:
        instrument_details = None
        instrument_id_value = None

        # Try to get calibration schedule and inventory item
        calibration_schedule = None

        # Method 1: Direct relationship
        if hasattr(log, 'schedule') and log.schedule:
            calibration_schedule = log.schedule
        # Method 2: Through ID
        elif hasattr(log, 'calibration_schedule_id') and log.calibration_schedule_id:
            calibration_schedule = CalibrationSchedule.get(id=log.calibration_schedule_id)
        # Method 3: Search by matching calibration logs (reverse lookup)
        else:
            # Find calibration schedule that has this log in its notification set
            calibration_schedule = select(cs for cs in CalibrationSchedule if log in cs.notification).first()

        if calibration_schedule and calibration_schedule.inventory_item:
            inventory_item = calibration_schedule.inventory_item
            instrument_id_value = inventory_item.id

            instrument_details = {
                "id": inventory_item.id,
                "item_code": inventory_item.item_code,
                "status": inventory_item.status,
                "quantity": inventory_item.quantity,
                "available_quantity": inventory_item.available_quantity,
                "subcategory_id": inventory_item.subcategory.id,
                "dynamic_data": inventory_item.dynamic_data,
                "subcategory_name": inventory_item.subcategory.name if hasattr(inventory_item.subcategory,
                                                                               'name') else None,
                "calibration_schedule_id": calibration_schedule.id,
                "calibration_type": calibration_schedule.calibration_type,
                "frequency_days": calibration_schedule.frequency_days,
                "last_calibration": calibration_schedule.last_calibration,
                "next_calibration": calibration_schedule.next_calibration
            }

        log_dict = {
            "id": log.id,
            "timestamp": log.timestamp,
            "calibration_due_date": log.calibration_due_date,
            "instrument_id": instrument_id_value,
            "instrument_details": instrument_details
        }
        result.append(log_dict)

    return result

@router.get("machine-status-logs", response_model=MachineNotificationsResponse)
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
                # Fetch username for the created_by user ID if it exists
                creator_info = entity.created_by
                if entity.created_by:
                    # Try to find the user by ID
                    user = select(u for u in User if str(u.id) == entity.created_by).first()
                    if user:
                        # Format as "user_id (username)"
                        creator_info = f"{entity.created_by} ({user.username})"

                # Only set acknowledger_info if the notification is acknowledged
                acknowledger_info = None  # Default to None when not acknowledged
                if entity.is_acknowledged and entity.acknowledged_by:
                    # Try to find the user by ID
                    user = select(u for u in User if str(u.id) == entity.acknowledged_by).first()
                    if user:
                        # Format as "user_id (username)"
                        acknowledger_info = f"{entity.acknowledged_by} ({user.username})"
                    else:
                        # If acknowledged but no user found, just use the ID
                        acknowledger_info = entity.acknowledged_by

                # Create notification with explicit ID and usernames
                notification = MachineNotification(
                    id=entity.id,
                    machine_id=entity.machine_id,
                    machine_make=entity.machine_make,
                    status_name=entity.status_name,
                    description=entity.description,
                    updated_at=entity.updated_at,
                    created_by=creator_info,
                    is_acknowledged=entity.is_acknowledged,
                    acknowledged_by=acknowledger_info,
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
@router.get("raw_material_status_logs", response_model=RawMaterialNotificationsResponse)
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
                # Fetch username for the created_by user ID if it exists
                creator_info = entity.created_by
                if entity.created_by:
                    # Try to find the user by ID
                    user = select(u for u in User if str(u.id) == entity.created_by).first()
                    if user:
                        # Format as "user_id (username)"
                        creator_info = f"{entity.created_by} ({user.username})"

                # Only set acknowledger_info if the notification is acknowledged
                acknowledger_info = None  # Default to None when not acknowledged
                if entity.is_acknowledged and entity.acknowledged_by:
                    # Try to find the user by ID
                    user = select(u for u in User if str(u.id) == entity.acknowledged_by).first()
                    if user:
                        # Format as "user_id (username)"
                        acknowledger_info = f"{entity.acknowledged_by} ({user.username})"
                    else:
                        # If acknowledged but no user found, just use the ID
                        acknowledger_info = entity.acknowledged_by

                # Create notification with explicit ID and usernames
                notification = RawMaterialNotification(
                    id=entity.id,  # Explicitly include notification ID
                    material_id=entity.material_id,
                    part_number=entity.part_number,
                    status_name=entity.status_name,
                    description=entity.description,
                    updated_at=entity.updated_at,
                    created_by=creator_info,
                    is_acknowledged=entity.is_acknowledged,
                    acknowledged_by=acknowledger_info,
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

class AcknowledgeRequest(BaseModel):
    id: int
    acknowledged_by: str  # Assuming string ID for user


@router.post("/machine-status-logs/acknowledge")
async def acknowledge_machine_notification(payload: AcknowledgeRequest):
    try:
        with db_session:
            notification = MachineStatusLog.get(id=payload.id)

            if not notification:
                raise HTTPException(status_code=404, detail="Machine notification not found")

            if notification.is_acknowledged:
                raise HTTPException(status_code=400, detail="Notification already acknowledged")

            notification.is_acknowledged = True
            notification.acknowledged_by = payload.acknowledged_by
            notification.acknowledged_at = datetime.now()

            # Send notification using notification ID instead of machine ID
            notification_data = {
                "notification_id": notification.id,  # Using notification ID
                "type": "machine_status_acknowledged",
                "machine_id": notification.machine_id,  # Still include machine_id for reference
                "acknowledged_by": payload.acknowledged_by,
                "acknowledged_at": notification.acknowledged_at.isoformat(),
                "status_name": notification.status_name,  # Fixed: using status_name instead of status
                "message": f"Machine notification {notification.id} has been acknowledged"
            }

            # Send notification with notification_id as prefix/identifier
            await send_notification(f"notification_{notification.id}", notification_data)

            return {
                "message": "Machine notification acknowledged successfully",
                "notification_id": notification.id,
                "acknowledged_at": notification.acknowledged_at
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error acknowledging machine notification: {str(e)}")


@router.post("/raw_material_status_logs/acknowledge")
async def acknowledge_raw_material_notification(payload: AcknowledgeRequest):
    try:
        with db_session:
            notification = RawMaterialStatusLog.get(id=payload.id)

            if not notification:
                raise HTTPException(status_code=404, detail="Raw material notification not found")

            if notification.is_acknowledged:
                raise HTTPException(status_code=400, detail="Notification already acknowledged")

            notification.is_acknowledged = True
            notification.acknowledged_by = payload.acknowledged_by
            notification.acknowledged_at = datetime.now()

            # Send notification using notification ID instead of raw material ID
            notification_data = {
                "notification_id": notification.id,  # Using notification ID
                "type": "raw_material_status_acknowledged",
                "material_id": notification.material_id,  # Still include material_id for reference
                "acknowledged_by": payload.acknowledged_by,
                "acknowledged_at": notification.acknowledged_at.isoformat(),
                "status_name": notification.status_name,  # Fixed: using status_name instead of status
                "message": f"Raw material notification {notification.id} has been acknowledged"
            }

            # Send notification with notification_id as prefix/identifier
            await send_notification(f"notification_{notification.id}", notification_data)

            return {
                "message": "Raw material notification acknowledged successfully",
                "notification_id": notification.id,
                "acknowledged_at": notification.acknowledged_at
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error acknowledging raw material notification: {str(e)}")


# Helper function for sending notifications (you'll need to implement this based on your notification system)
async def send_notification(prefix: str, data: dict):
    """
    Send notification with notification ID as prefix instead of machine/raw_material ID

    Args:
        prefix: notification prefix (e.g., "notification_123")
        data: notification data dictionary
    """
    # Implementation depends on your notification system
    # This could be WebSocket, Redis pub/sub, etc.
    print(f"Sending notification with prefix: {prefix}")
    print(f"Notification data: {data}")
    # Your notification sending logic here