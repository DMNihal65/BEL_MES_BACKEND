from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends
from typing import List, Dict, Any, Optional
from pony.orm import db_session, select, commit, desc
from datetime import datetime
import json
import asyncio
from app.models.logs import (
    NotificationLog, MachineStatusLog, RawMaterialStatusLog,
    MachineCalibrationLog, InstrumentCalibrationLog, PokaYokeCompletedLog
)

router = APIRouter(prefix="/api/v1/simple-notification", tags=["simple-notification"])

# Store active WebSocket connections
active_connections: List[WebSocket] = []


# Custom JSON encoder to handle datetime objects
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


async def connect(websocket: WebSocket):
    """Add a new WebSocket connection"""
    await websocket.accept()
    active_connections.append(websocket)
    print(f"New WebSocket connection added. Total connections: {len(active_connections)}")


def disconnect(websocket: WebSocket):
    """Remove a WebSocket connection"""
    if websocket in active_connections:
        active_connections.remove(websocket)
        print(f"WebSocket connection removed. Total connections: {len(active_connections)}")


async def broadcast_notification(notification_data: Dict[str, Any]):
    """Broadcast notification to all connected clients"""
    if not active_connections:
        print("No active WebSocket connections")
        return

    try:
        json_message = json.dumps(notification_data, cls=DateTimeEncoder)
        print(f"Broadcasting notification: {json_message}")

        failed_connections = []
        for connection in active_connections:
            try:
                await connection.send_text(json_message)
                print(f"Notification sent to client")
            except Exception as e:
                print(f"Error sending to WebSocket: {str(e)}")
                failed_connections.append(connection)

        # Remove failed connections
        for connection in failed_connections:
            disconnect(connection)

    except Exception as e:
        print(f"Error broadcasting notification: {str(e)}")


def create_notification(source_table: str, source_id: int, notification_type: str, title: str, message: str):
    """Create a new notification in the database"""
    try:
        with db_session:
            notification = NotificationLog(
                source_table=source_table,
                source_id=source_id,
                notification_type=notification_type,
                title=title,
                message=message
            )
            commit()

            # Schedule async broadcast
            asyncio.create_task(broadcast_notification(notification.to_dict()))

            print(f"Notification created: {notification.id}")
            return notification
    except Exception as e:
        print(f"Error creating notification: {str(e)}")
        return None


# WebSocket endpoint for all notifications
@router.websocket("/ws/notifications")
async def notifications_websocket(websocket: WebSocket):
    await connect(websocket)
    try:
        # Send initial unread notifications
        with db_session:
            unread_notifications = list(
                select(n for n in NotificationLog if not n.is_read).order_by(desc(NotificationLog.created_at)))

            if unread_notifications:
                notification_dicts = [n.to_dict() for n in unread_notifications]
                await websocket.send_json({
                    "type": "initial_notifications",
                    "total_notifications": len(notification_dicts),
                    "notifications": notification_dicts
                })
                print(f"Sent {len(notification_dicts)} initial notifications")

        # Listen for messages (mark as read)
        while True:
            data = await websocket.receive_json()

            if data.get("type") == "mark_read":
                notification_id = data.get("notification_id")
                user_id = data.get("user_id")

                if notification_id and user_id:
                    with db_session:
                        notification = NotificationLog.get(id=notification_id)
                        if notification and not notification.is_read:
                            notification.is_read = True
                            notification.read_by = user_id
                            notification.read_at = datetime.now()
                            commit()

                            # Broadcast read status to all clients
                            read_message = {
                                "type": "notification_read",
                                "notification_id": notification_id,
                                "read_by": user_id,
                                "read_at": datetime.now().isoformat()
                            }
                            await broadcast_notification(read_message)

                            await websocket.send_json({"status": "success", "message": "Notification marked as read"})
                        else:
                            await websocket.send_json(
                                {"status": "error", "message": "Notification not found or already read"})
                else:
                    await websocket.send_json({"status": "error", "message": "Invalid data"})

    except WebSocketDisconnect:
        disconnect(websocket)
    except Exception as e:
        print(f"WebSocket error: {str(e)}")
        disconnect(websocket)


# REST endpoint to get unread notifications
@router.get("/unread")
async def get_unread_notifications():
    """Get all unread notifications"""
    try:
        with db_session:
            unread_notifications = list(
                select(n for n in NotificationLog if not n.is_read).order_by(desc(NotificationLog.created_at)))
            return {
                "status": "success",
                "total_notifications": len(unread_notifications),
                "notifications": [n.to_dict() for n in unread_notifications]
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching notifications: {str(e)}")


# REST endpoint to mark notification as read
@router.post("/mark-read")
async def mark_notification_read(notification_id: int, user_id: str):
    """Mark a notification as read"""
    try:
        with db_session:
            notification = NotificationLog.get(id=notification_id)
            if not notification:
                raise HTTPException(status_code=404, detail="Notification not found")

            if notification.is_read:
                return {"status": "already_read", "message": "Notification already read"}

            notification.is_read = True
            notification.read_by = user_id
            notification.read_at = datetime.now()
            commit()

            # Broadcast read status
            read_message = {
                "type": "notification_read",
                "notification_id": notification_id,
                "read_by": user_id,
                "read_at": datetime.now().isoformat()
            }
            asyncio.create_task(broadcast_notification(read_message))

            return {"status": "success", "message": "Notification marked as read"}

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error marking notification as read: {str(e)}")


# Functions to be called from other services to create notifications
def notify_machine_status(machine_id: int, machine_make: str, status_name: str, description: str = None):
    """Create notification for machine status changes"""
    title = f"Machine Status Update - {machine_make}"
    message = f"Machine {machine_make} status changed to: {status_name}"
    if description:
        message += f" - {description}"

    return create_notification(
        source_table="machine_status_logs",
        source_id=machine_id,
        notification_type="machine_status",
        title=title,
        message=message
    )


def notify_raw_material_status(material_id: int, part_number: str, status_name: str, description: str = None):
    """Create notification for raw material status changes"""
    title = f"Raw Material Status Update - {part_number or material_id}"
    message = f"Material {part_number or material_id} status changed to: {status_name}"
    if description:
        message += f" - {description}"

    return create_notification(
        source_table="raw_material_status_logs",
        source_id=material_id,
        notification_type="raw_material_status",
        title=title,
        message=message
    )


def notify_machine_calibration(machine_id: int, calibration_due_date: str = None):
    """Create notification for machine calibration"""
    title = "Machine Calibration Required"
    message = f"Machine {machine_id} requires calibration"
    if calibration_due_date:
        message += f" - Due: {calibration_due_date}"

    return create_notification(
        source_table="machine_calibration_logs",
        source_id=machine_id,
        notification_type="machine_calibration",
        title=title,
        message=message
    )


def notify_instrument_calibration(instrument_id: int, calibration_due_date: str = None):
    """Create notification for instrument calibration"""
    title = "Instrument Calibration Required"
    message = f"Instrument {instrument_id} requires calibration"
    if calibration_due_date:
        message += f" - Due: {calibration_due_date}"

    return create_notification(
        source_table="instrument_calibration_logs",
        source_id=instrument_id,
        notification_type="instrument_calibration",
        title=title,
        message=message
    )


def notify_pokayoke_completed(checklist_id: int, machine_id: int, operator_id: str, all_passed: bool):
    """Create notification for PokaYoke checklist completion"""
    status = "PASSED" if all_passed else "FAILED"
    title = f"PokaYoke Checklist {status} - Machine {machine_id}"
    message = f"PokaYoke checklist completed by {operator_id} - Status: {status}"

    return create_notification(
        source_table="pokayoke_completed_logs",
        source_id=checklist_id,
        notification_type="pokayoke_completed",
        title=title,
        message=message
    )