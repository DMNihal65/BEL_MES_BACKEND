from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends, Query, Body
from typing import List, Dict, Any, Optional
from pony.orm import db_session, select, commit
from datetime import datetime
import json
from app.models.logs import MachineStatusLog, RawMaterialStatusLog
from app.schemas.comp_maintainance import NotificationAcknowledgmentRequest

# router = APIRouter()

router = APIRouter(prefix="/api/v1/notification", tags=["notification"])

# Store active WebSocket connections
active_connections: Dict[str, List[WebSocket]] = {
    "machine_notifications": [],
    "material_notifications": []
}

# Custom JSON encoder to handle datetime objects
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

# Function to add a connection to the manager
async def connect(websocket: WebSocket, connection_type: str):
    await websocket.accept()
    if connection_type in active_connections:
        active_connections[connection_type].append(websocket)
    else:
        active_connections[connection_type] = [websocket]

# Function to remove a connection from the manager
def disconnect(websocket: WebSocket, connection_type: str):
    if connection_type in active_connections:
        if websocket in active_connections[connection_type]:
            active_connections[connection_type].remove(websocket)

# Function to safely convert entity to dict
def entity_to_dict(entity):
    """Convert an entity to a dict with proper datetime handling"""
    if hasattr(entity, 'to_dict'):
        # Use a database session to ensure the entity is attached
        with db_session:
            # Get the attached entity
            refreshed_entity = type(entity).get(id=entity.id)
            if not refreshed_entity:
                return {}

            # Convert to dict
            entity_dict = {}
            # Always include the ID
            entity_dict["id"] = refreshed_entity.id

            # Add all other fields
            for key, value in refreshed_entity.to_dict().items():
                if isinstance(value, datetime):
                    entity_dict[key] = value.isoformat()
                else:
                    entity_dict[key] = value
            return entity_dict
    return {}

# Function to broadcast a message to all connected clients for a specific type
async def broadcast(message: Dict[str, Any], connection_type: str):
    if connection_type not in active_connections:
        print(f"Warning: No active connections for {connection_type}")
        return

    if not active_connections[connection_type]:
        print(f"Warning: Empty connection list for {connection_type}")
        return

    # Serialize message with custom encoder for datetime objects
    try:
        json_message = json.dumps(message, cls=DateTimeEncoder)
    except Exception as e:
        print(f"Error serializing message: {str(e)}")
        print(f"Message content: {message}")
        return

    failed_connections = []
    successful_connections = 0

    for connection in active_connections[connection_type]:
        try:
            await connection.send_text(json_message)
            successful_connections += 1
        except Exception as e:
            print(f"Error sending to WebSocket: {str(e)}")
            # Track failed connections for removal
            failed_connections.append(connection)

    # Remove failed connections
    for connection in failed_connections:
        disconnect(connection, connection_type)

    if failed_connections:
        print(f"Removed {len(failed_connections)} failed connections from {connection_type}")

    if successful_connections:
        print(f"Successfully sent message to {successful_connections} clients of type {connection_type}")

# WebSocket endpoint for machine notifications
@router.websocket("/ws/machine-notifications")
async def machine_notifications_ws(websocket: WebSocket):
    await connect(websocket, "machine_notifications")
    try:
        # Send initial unacknowledged notifications
        with db_session:
            notifications = list(select(n for n in MachineStatusLog if not n.is_acknowledged))
            if notifications:
                # Convert entity to dict within the db_session
                notification_dicts = []
                for n in notifications:
                    n_dict = entity_to_dict(n)
                    # Ensure ID is included
                    notification_dicts.append({
                        "id": n.id,
                        **n_dict
                    })

                await websocket.send_json({
                    "type": "initial_notifications",
                    "total_notifications": len(notification_dicts),
                    "notifications": notification_dicts
                })

        # Listen for messages (acknowledgments)
        while True:
            data = await websocket.receive_json()

            # Handle acknowledgments
            if data.get("type") == "acknowledge":
                notification_id = data.get("notification_id")
                user_id = data.get("user_id")

                if notification_id and user_id:
                    with db_session:
                        notification = MachineStatusLog.get(id=notification_id)
                        if notification and not notification.is_acknowledged:
                            # Get notification details before updating
                            notification_dict = entity_to_dict(notification)

                            # Update notification
                            notification.is_acknowledged = True
                            notification.acknowledged_by = user_id
                            notification.acknowledged_at = datetime.now()

                            # Save changes and get the current state
                            commit()

                            # Broadcast the acknowledgment to all connected clients
                            ack_message = {
                                "type": "notification_acknowledged",
                                "notification_id": notification_id,
                                "acknowledged_by": user_id,
                                "acknowledged_at": datetime.now().isoformat(),
                                "notification": notification_dict  # Include the original notification data
                            }
                            await broadcast(ack_message, "machine_notifications")

                            await websocket.send_json({"status": "success", "message": "Notification acknowledged"})
                        else:
                            await websocket.send_json({"status": "error", "message": "Notification not found or already acknowledged"})
                else:
                    await websocket.send_json({"status": "error", "message": "Invalid data"})

    except WebSocketDisconnect:
        disconnect(websocket, "machine_notifications")
    except Exception as e:
        print(f"WebSocket error: {str(e)}")
        disconnect(websocket, "machine_notifications")

# WebSocket endpoint for raw material notifications
@router.websocket("/ws/material-notifications")
async def material_notifications_ws(websocket: WebSocket):
    await connect(websocket, "material_notifications")
    try:
        # Send initial unacknowledged notifications
        with db_session:
            notifications = list(select(n for n in RawMaterialStatusLog if not n.is_acknowledged))
            if notifications:
                # Convert entity to dict within the db_session
                notification_dicts = []
                for n in notifications:
                    n_dict = entity_to_dict(n)
                    # Ensure ID is included
                    notification_dicts.append({
                        "id": n.id,
                        **n_dict
                    })

                await websocket.send_json({
                    "type": "initial_notifications",
                    "total_notifications": len(notification_dicts),
                    "notifications": notification_dicts
                })

        # Listen for messages (acknowledgments)
        while True:
            data = await websocket.receive_json()

            # Handle acknowledgments
            if data.get("type") == "acknowledge":
                notification_id = data.get("notification_id")
                user_id = data.get("user_id")

                if notification_id and user_id:
                    with db_session:
                        notification = RawMaterialStatusLog.get(id=notification_id)
                        if notification and not notification.is_acknowledged:
                            # Get notification details before updating
                            notification_dict = entity_to_dict(notification)

                            # Update notification
                            notification.is_acknowledged = True
                            notification.acknowledged_by = user_id
                            notification.acknowledged_at = datetime.now()

                            # Save changes
                            commit()

                            # Broadcast the acknowledgment to all connected clients
                            ack_message = {
                                "type": "notification_acknowledged",
                                "notification_id": notification_id,
                                "acknowledged_by": user_id,
                                "acknowledged_at": datetime.now().isoformat(),
                                "notification": notification_dict  # Include the original notification data
                            }
                            await broadcast(ack_message, "material_notifications")

                            await websocket.send_json({"status": "success", "message": "Notification acknowledged"})
                        else:
                            await websocket.send_json({"status": "error", "message": "Notification not found or already acknowledged"})
                else:
                    await websocket.send_json({"status": "error", "message": "Invalid data"})

    except WebSocketDisconnect:
        disconnect(websocket, "material_notifications")
    except Exception as e:
        print(f"WebSocket error: {str(e)}")
        disconnect(websocket, "material_notifications")

# REST endpoint to acknowledge a machine notification
@router.post("/machine-notification/acknowledge", status_code=200)
async def acknowledge_machine_notification(request: NotificationAcknowledgmentRequest):
    """
    Acknowledge a machine notification.
    This endpoint is useful for clients that don't support WebSockets.
    """
    try:
        with db_session:
            notification = MachineStatusLog.get(id=request.notification_id)
            if not notification:
                raise HTTPException(status_code=404, detail=f"Notification with ID {request.notification_id} not found")

            if notification.is_acknowledged:
                return {"status": "already_acknowledged", "message": "Notification already acknowledged"}

            # Get notification details before updating
            notification_id = notification.id
            notification_dict = entity_to_dict(notification)

            # Update notification
            notification.is_acknowledged = True
            notification.acknowledged_by = request.user_id
            notification.acknowledged_at = datetime.now()
            commit()

            # Prepare broadcast message
            ack_message = {
                "type": "notification_acknowledged",
                "notification_id": notification_id,
                "acknowledged_by": request.user_id,
                "acknowledged_at": datetime.now().isoformat(),
                "notification": notification_dict  # Include the original notification data
            }

            # Use background task to broadcast the message
            # This prevents issues with async in synchronous context
            import asyncio
            asyncio.create_task(broadcast(ack_message, "machine_notifications"))

            return {"status": "success", "message": "Notification acknowledged successfully"}

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"Error acknowledging notification: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error acknowledging notification: {str(e)}")

# REST endpoint to acknowledge a raw material notification
@router.post("/material-notification/acknowledge", status_code=200)
async def acknowledge_material_notification(request: NotificationAcknowledgmentRequest):
    """
    Acknowledge a raw material notification.
    This endpoint is useful for clients that don't support WebSockets.
    """
    try:
        with db_session:
            notification = RawMaterialStatusLog.get(id=request.notification_id)
            if not notification:
                raise HTTPException(status_code=404, detail=f"Notification with ID {request.notification_id} not found")

            if notification.is_acknowledged:
                return {"status": "already_acknowledged", "message": "Notification already acknowledged"}

            # Get notification details before updating
            notification_id = notification.id
            notification_dict = entity_to_dict(notification)

            # Update notification
            notification.is_acknowledged = True
            notification.acknowledged_by = request.user_id
            notification.acknowledged_at = datetime.now()
            commit()

            # Prepare broadcast message
            ack_message = {
                "type": "notification_acknowledged",
                "notification_id": notification_id,
                "acknowledged_by": request.user_id,
                "acknowledged_at": datetime.now().isoformat(),
                "notification": notification_dict  # Include the original notification data
            }

            # Use background task to broadcast the message
            # This prevents issues with async in synchronous context
            import asyncio
            asyncio.create_task(broadcast(ack_message, "material_notifications"))

            return {"status": "success", "message": "Notification acknowledged successfully"}

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"Error acknowledging notification: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error acknowledging notification: {str(e)}")

# REST endpoint to get unacknowledged machine notifications
@router.get("/machine-notifications/unacknowledged")
async def get_unacknowledged_machine_notifications():
    """
    Get all unacknowledged machine notifications.
    Useful for initially populating the UI.
    """
    try:
        with db_session:
            notifications = list(select(n for n in MachineStatusLog if not n.is_acknowledged))
            notification_dicts = []
            for n in notifications:
                n_dict = entity_to_dict(n)
                # Ensure ID is included
                notification_dicts.append({
                    "id": n.id,
                    **n_dict
                })

            return {
                "total_notifications": len(notification_dicts),
                "notifications": notification_dicts
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching notifications: {str(e)}")

# REST endpoint to get unacknowledged raw material notifications
@router.get("/material-notifications/unacknowledged")
async def get_unacknowledged_material_notifications():
    """
    Get all unacknowledged raw material notifications.
    Useful for initially populating the UI.
    """
    try:
        with db_session:
            notifications = list(select(n for n in RawMaterialStatusLog if not n.is_acknowledged))
            notification_dicts = []
            for n in notifications:
                n_dict = entity_to_dict(n)
                # Ensure ID is included
                notification_dicts.append({
                    "id": n.id,
                    **n_dict
                })

            return {
                "total_notifications": len(notification_dicts),
                "notifications": notification_dicts
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching notifications: {str(e)}")

# Function to send notification when new log entries are created
async def send_notification(log_entry, notification_type):
    """
    Send notification to all connected WebSocket clients when a new log entry is created.
    This function should be called after a new log entry is added to the database.
    """
    try:
        if not log_entry:
            print(f"Error: Null log entry passed to send_notification for {notification_type}")
            return

        # Get the ID early
        notification_id = log_entry.id if hasattr(log_entry, 'id') else None

        if not notification_id:
            print(f"Error: Log entry has no ID: {log_entry}")
            return

        # Convert the log entry to a dict with proper datetime handling
        notification_dict = entity_to_dict(log_entry)

        if not notification_dict:
            print(f"Error: Could not convert log entry to dict: {log_entry}")
            return

        # Ensure notification_id is included in the dict
        if 'id' not in notification_dict:
            notification_dict['id'] = notification_id

        notification_data = {
            "type": "new_notification",
            "notification_id": notification_id,
            "notification": notification_dict,
            # Include a timestamp for when the notification was sent
            "sent_at": datetime.now().isoformat()
        }

        # Determine how many clients will receive this notification
        client_count = 0
        if notification_type == "machine":
            client_count = len(active_connections.get("machine_notifications", []))
        elif notification_type == "material":
            client_count = len(active_connections.get("material_notifications", []))

        print(f"Sending {notification_type} notification ID {notification_id} to {client_count} clients")

        if notification_type == "machine":
            await broadcast(notification_data, "machine_notifications")
        elif notification_type == "material":
            await broadcast(notification_data, "material_notifications")

        print(f"Successfully sent {notification_type} notification ID {notification_id}")

    except Exception as e:
        print(f"Error sending notification: {str(e)}")
        import traceback
        traceback.print_exc()