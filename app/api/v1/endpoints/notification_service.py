from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends, Query, Body
from typing import List, Dict, Any, Optional
from pony.orm import db_session, select, commit, desc
from datetime import datetime, date
import json
from app.models.logs import MachineStatusLog, RawMaterialStatusLog, MachineCalibrationLog, InstrumentCalibrationLog
from app.schemas.comp_maintainance import NotificationAcknowledgmentRequest

# router = APIRouter()

router = APIRouter(prefix="/api/v1/notification", tags=["notification"])

# Store active WebSocket connections
active_connections: Dict[str, List[WebSocket]] = {
    "machine_notifications": [],
    "material_notifications": [],
    "calibration_notifications": [],
    "instrument_calibration_notifications": []  # Added new connection type
}


# Custom JSON encoder to handle datetime objects
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


# Function to add a connection to the manager
async def connect(websocket: WebSocket, connection_type: str):
    print(f"Accepting WebSocket connection for {connection_type}")
    await websocket.accept()
    if connection_type in active_connections:
        active_connections[connection_type].append(websocket)
        print(f"Added connection to {connection_type}. Total connections: {len(active_connections[connection_type])}")
    else:
        active_connections[connection_type] = [websocket]
        print(f"Created new connection group for {connection_type}")


# Function to remove a connection from the manager
def disconnect(websocket: WebSocket, connection_type: str):
    print(f"Disconnecting WebSocket from {connection_type}")
    if connection_type in active_connections:
        if websocket in active_connections[connection_type]:
            active_connections[connection_type].remove(websocket)
            print(
                f"Removed connection from {connection_type}. Remaining connections: {len(active_connections[connection_type])}")
        else:
            print(f"WebSocket not found in {connection_type} connections")
    else:
        print(f"No active connections for {connection_type}")


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
    print(f"Broadcasting message for {connection_type}. Message: {message}")  # Debug log

    if connection_type not in active_connections:
        print(f"Warning: No active connections for {connection_type}")
        return

    if not active_connections[connection_type]:
        print(f"Warning: Empty connection list for {connection_type}")
        return

    # Serialize message with custom encoder for datetime objects
    try:
        json_message = json.dumps(message, cls=DateTimeEncoder)
        print(f"Serialized message: {json_message}")  # Debug log
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
            print(f"Successfully sent message to a client")  # Debug log
        except Exception as e:
            print(f"Error sending to WebSocket: {str(e)}")
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
                            await websocket.send_json(
                                {"status": "error", "message": "Notification not found or already acknowledged"})
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
                            await websocket.send_json(
                                {"status": "error", "message": "Notification not found or already acknowledged"})
                else:
                    await websocket.send_json({"status": "error", "message": "Invalid data"})

    except WebSocketDisconnect:
        disconnect(websocket, "material_notifications")
    except Exception as e:
        print(f"WebSocket error: {str(e)}")
        disconnect(websocket, "material_notifications")


# WebSocket endpoint for machine calibration notifications
@router.websocket("/ws/calibration-notifications")
async def calibration_notifications_ws(websocket: WebSocket):
    print("New WebSocket connection request for calibration notifications")
    await connect(websocket, "calibration_notifications")
    try:
        # Send initial notifications for today only
        with db_session:
            today = date.today()
            print(f"Fetching today's calibration notifications for {today}")

            # Filter for notifications created today
            notifications = list(select(n for n in MachineCalibrationLog
                                        if n.timestamp.date() == today))

            print(f"Found {len(notifications)} calibration notifications for today")

            if notifications:
                notification_dicts = []
                for n in notifications:
                    # Get machine details
                    machine_id = None
                    machine_name = None
                    machine_type = None
                    machine_make = None

                    if n.machine_id:
                        try:
                            from app.models.master_order import Machine
                            machine = Machine.get(id=n.machine_id.id if hasattr(n.machine_id, 'id') else n.machine_id)
                            if machine:
                                machine_id = machine.id
                                machine_type = machine.type
                                machine_make = machine.make
                                machine_name = f"{machine.make} {machine.model}"
                        except Exception as e:
                            print(f"Error fetching machine details: {str(e)}")
                            # If error occurs, use the original machine_id
                            machine_id = n.machine_id.id if hasattr(n.machine_id, 'id') else n.machine_id

                    notification_dict = {
                        "id": n.id,
                        "timestamp": n.timestamp.isoformat(),
                        "calibration_due_date": n.calibration_due_date.isoformat() if n.calibration_due_date else None,
                        "machine_id": machine_id,
                        "machine_name": machine_name,
                        "machine_type": machine_type,
                        "machine_make": machine_make
                    }
                    notification_dicts.append(notification_dict)

                print(f"Sending {len(notification_dicts)} today's notifications to client")
                await websocket.send_json({
                    "type": "initial_notifications",
                    "total_notifications": len(notification_dicts),
                    "notifications": notification_dicts
                })
                print("Today's initial notifications sent successfully")

        # Keep the connection alive and wait for disconnection
        print("Entering WebSocket receive loop")
        while True:
            message = await websocket.receive_text()
            print(f"Received message from client: {message}")

    except WebSocketDisconnect:
        print("WebSocket disconnected")
        disconnect(websocket, "calibration_notifications")
    except Exception as e:
        print(f"WebSocket error: {str(e)}")
        import traceback
        traceback.print_exc()
        disconnect(websocket, "calibration_notifications")


# WebSocket endpoint for instrument calibration notifications
@router.websocket("/ws/instrument-calibration-notifications")
async def instrument_calibration_notifications_ws(websocket: WebSocket):
    print("New WebSocket connection request for instrument calibration notifications")
    await connect(websocket, "instrument_calibration_notifications")
    try:
        # Send initial notifications for today only
        with db_session:
            today = date.today()
            print(f"Fetching today's instrument calibration notifications for {today}")

            # Filter for notifications created today
            notifications = list(select(n for n in InstrumentCalibrationLog
                                        if n.timestamp.date() == today))

            print(f"Found {len(notifications)} instrument calibration notifications for today")

            if notifications:
                notification_dicts = []
                for n in notifications:
                    # Get instrument details
                    instrument_id = None
                    item_name = None
                    last_calibration = None
                    next_calibration = None
                    calibration_type = None
                    trade_name = None
                    bel_part_number = None

                    if n.instrument_id:
                        try:
                            from app.models.inventoryv1 import CalibrationSchedule, InventoryItem
                            calib_schedule = CalibrationSchedule.get(
                                id=n.instrument_id.id if hasattr(n.instrument_id, 'id') else n.instrument_id)
                            if calib_schedule:
                                instrument_id = calib_schedule.id
                                last_calibration = calib_schedule.last_calibration.isoformat() if calib_schedule.last_calibration else None
                                next_calibration = calib_schedule.next_calibration.isoformat() if calib_schedule.next_calibration else None
                                calibration_type = calib_schedule.calibration_type

                                # Get item name from inventory item
                                if calib_schedule.inventory_item:
                                    inv_item = calib_schedule.inventory_item
                                    item_name = inv_item.item_code

                                    # Extract additional details from dynamic_data
                                    if hasattr(inv_item, 'dynamic_data') and inv_item.dynamic_data:
                                        try:
                                            dynamic_data = inv_item.dynamic_data
                                            if isinstance(dynamic_data, dict):
                                                # Try to get name from dynamic data
                                                if 'name' in dynamic_data:
                                                    item_name = dynamic_data['name']

                                                # Get Trade Name
                                                if 'Trade Name' in dynamic_data:
                                                    trade_name = dynamic_data['Trade Name']

                                                # Get BEL Part Number (note the space at the end in the key)
                                                if 'BEL Part Number ' in dynamic_data:
                                                    bel_part_number = dynamic_data['BEL Part Number ']
                                                elif 'BEL Part Number' in dynamic_data:  # Try without space too
                                                    bel_part_number = dynamic_data['BEL Part Number']
                                        except Exception as e:
                                            print(f"Error parsing dynamic_data for instrument {inv_item.id}: {str(e)}")
                        except Exception as e:
                            print(f"Error fetching instrument details: {str(e)}")
                            instrument_id = n.instrument_id.id if hasattr(n.instrument_id, 'id') else n.instrument_id

                    notification_dict = {
                        "id": n.id,
                        "timestamp": n.timestamp.isoformat(),
                        "calibration_due_date": n.calibration_due_date.isoformat() if n.calibration_due_date else None,
                        "instrument_id": instrument_id,
                        "item_name": item_name,
                        "last_calibration": last_calibration,
                        "next_calibration": next_calibration,
                        "calibration_type": calibration_type,
                        "trade_name": trade_name,
                        "bel_part_number": bel_part_number
                    }
                    notification_dicts.append(notification_dict)

                print(f"Sending {len(notification_dicts)} today's instrument notifications to client")
                await websocket.send_json({
                    "type": "initial_notifications",
                    "total_notifications": len(notification_dicts),
                    "notifications": notification_dicts
                })
                print("Today's initial instrument notifications sent successfully")

        # Keep the connection alive and wait for disconnection
        print("Entering instrument WebSocket receive loop")
        while True:
            message = await websocket.receive_text()
            print(f"Received message from instrument client: {message}")

    except WebSocketDisconnect:
        print("Instrument WebSocket disconnected")
        disconnect(websocket, "instrument_calibration_notifications")
    except Exception as e:
        print(f"Instrument WebSocket error: {str(e)}")
        import traceback
        traceback.print_exc()
        disconnect(websocket, "instrument_calibration_notifications")


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


# Function to send calibration notification when new log entries are created
async def send_calibration_notification(log_entry):
    """
    Send notification to all connected WebSocket clients when a new calibration log entry is created.
    This function should be called after a new log entry is added to the database.
    """
    print(f"send_calibration_notification called with log_entry: {log_entry}")

    try:
        if not log_entry:
            print("Error: Null log entry passed to send_calibration_notification")
            return

        # Get machine details
        machine_id = None
        machine_name = None
        machine_type = None
        machine_make = None

        if log_entry.machine_id:
            try:
                from app.models.master_order import Machine
                with db_session:
                    machine = Machine.get(
                        id=log_entry.machine_id.id if hasattr(log_entry.machine_id, 'id') else log_entry.machine_id)
                    if machine:
                        machine_id = machine.id
                        machine_type = machine.type
                        machine_make = machine.make
                        machine_name = f"{machine.make} {machine.model}"
            except Exception as e:
                print(f"Error fetching machine details: {str(e)}")
                machine_id = log_entry.machine_id.id if hasattr(log_entry.machine_id, 'id') else log_entry.machine_id

        print(f"Processing notification for machine: {machine_name} (ID: {machine_id})")

        notification_dict = {
            "id": log_entry.id,
            "timestamp": log_entry.timestamp.isoformat(),
            "calibration_due_date": log_entry.calibration_due_date.isoformat() if log_entry.calibration_due_date else None,
            "machine_id": machine_id,
            "machine_name": machine_name,
            "machine_type": machine_type,
            "machine_make": machine_make
        }

        notification_data = {
            "type": "new_notification",
            "notification": notification_dict,
            "sent_at": datetime.now().isoformat()
        }

        # Get count of connected clients
        client_count = len(active_connections.get("calibration_notifications", []))
        print(f"Found {client_count} connected clients")

        await broadcast(notification_data, "calibration_notifications")
        print(f"Successfully sent calibration notification ID {log_entry.id}")

    except Exception as e:
        print(f"Error sending calibration notification: {str(e)}")
        import traceback
        traceback.print_exc()


# Function to send instrument calibration notification when new log entries are created
async def send_instrument_calibration_notification(log_entry):
    """
    Send notification to all connected WebSocket clients when a new instrument calibration log entry is created.
    This function should be called after a new log entry is added to the database.
    """
    print(f"send_instrument_calibration_notification called with log_entry: {log_entry}")

    try:
        if not log_entry:
            print("Error: Null log entry passed to send_instrument_calibration_notification")
            return

        # Get instrument details
        instrument_id = None
        item_name = None
        last_calibration = None
        next_calibration = None
        calibration_type = None
        trade_name = None
        bel_part_number = None

        if log_entry.instrument_id:
            try:
                from app.models.inventoryv1 import CalibrationSchedule, InventoryItem
                with db_session:
                    calib_schedule = CalibrationSchedule.get(
                        id=log_entry.instrument_id.id if hasattr(log_entry.instrument_id,
                                                                 'id') else log_entry.instrument_id)
                    if calib_schedule:
                        instrument_id = calib_schedule.id
                        last_calibration = calib_schedule.last_calibration.isoformat() if calib_schedule.last_calibration else None
                        next_calibration = calib_schedule.next_calibration.isoformat() if calib_schedule.next_calibration else None
                        calibration_type = calib_schedule.calibration_type

                        # Get item name from inventory item
                        if calib_schedule.inventory_item:
                            inv_item = calib_schedule.inventory_item
                            item_name = inv_item.item_code

                            # Extract additional details from dynamic_data
                            if hasattr(inv_item, 'dynamic_data') and inv_item.dynamic_data:
                                try:
                                    dynamic_data = inv_item.dynamic_data
                                    if isinstance(dynamic_data, dict):
                                        # Try to get name from dynamic data
                                        if 'name' in dynamic_data:
                                            item_name = dynamic_data['name']

                                        # Get Trade Name
                                        if 'Trade Name' in dynamic_data:
                                            trade_name = dynamic_data['Trade Name']

                                        # Get BEL Part Number (note the space at the end in the key)
                                        if 'BEL Part Number ' in dynamic_data:
                                            bel_part_number = dynamic_data['BEL Part Number ']
                                        elif 'BEL Part Number' in dynamic_data:  # Try without space too
                                            bel_part_number = dynamic_data['BEL Part Number']
                                except Exception as e:
                                    print(f"Error parsing dynamic_data for instrument {inv_item.id}: {str(e)}")
            except Exception as e:
                print(f"Error fetching instrument details: {str(e)}")
                instrument_id = log_entry.instrument_id.id if hasattr(log_entry.instrument_id,
                                                                      'id') else log_entry.instrument_id

        print(f"Processing notification for instrument: {item_name} (ID: {instrument_id})")

        notification_dict = {
            "id": log_entry.id,
            "timestamp": log_entry.timestamp.isoformat(),
            "calibration_due_date": log_entry.calibration_due_date.isoformat() if log_entry.calibration_due_date else None,
            "instrument_id": instrument_id,
            "item_name": item_name,
            "last_calibration": last_calibration,
            "next_calibration": next_calibration,
            "calibration_type": calibration_type,
            "trade_name": trade_name,
            "bel_part_number": bel_part_number
        }

        notification_data = {
            "type": "new_notification",
            "notification": notification_dict,
            "sent_at": datetime.now().isoformat()
        }

        # Get count of connected clients
        client_count = len(active_connections.get("instrument_calibration_notifications", []))
        print(f"Found {client_count} connected instrument clients")

        await broadcast(notification_data, "instrument_calibration_notifications")
        print(f"Successfully sent instrument calibration notification ID {log_entry.id}")

    except Exception as e:
        print(f"Error sending instrument calibration notification: {str(e)}")
        import traceback
        traceback.print_exc()


# REST endpoint to test calibration notification
@router.post("/test-calibration-notification")
async def test_calibration_notification():
    """
    Test endpoint to simulate a new calibration notification
    """
    try:
        with db_session:
            # Create a test notification without machine_id
            test_log = MachineCalibrationLog(
                timestamp=datetime.now(),
                calibration_due_date=datetime.now().date(),
                machine_id=None  # Set to None to avoid foreign key error
            )
            commit()

            # We need to retrieve the saved entity to ensure it's fully loaded
            saved_log = MachineCalibrationLog.get(id=test_log.id)

            print(f"Created test log with ID: {saved_log.id}")

            # Send the notification
            await send_calibration_notification(saved_log)

            return {"status": "success", "message": f"Test notification sent with ID: {saved_log.id}"}
    except Exception as e:
        print(f"Error in test endpoint: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# REST endpoint to test instrument calibration notification
@router.post("/test-instrument-calibration-notification")
async def test_instrument_calibration_notification():
    """
    Test endpoint to simulate a new instrument calibration notification
    """
    try:
        with db_session:
            # Create a test notification without instrument_id
            test_log = InstrumentCalibrationLog(
                timestamp=datetime.now(),
                calibration_due_date=datetime.now().date(),
                instrument_id=None  # Set to None to avoid foreign key error
            )
            commit()

            # We need to retrieve the saved entity to ensure it's fully loaded
            saved_log = InstrumentCalibrationLog.get(id=test_log.id)

            print(f"Created test instrument log with ID: {saved_log.id}")

            # Send the notification
            await send_instrument_calibration_notification(saved_log)

            return {"status": "success", "message": f"Test instrument notification sent with ID: {saved_log.id}"}
    except Exception as e:
        print(f"Error in test instrument endpoint: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# REST endpoint to get machine calibration notifications
@router.get("/machine-calibrations")
async def get_machine_calibration_notifications(
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        limit: int = Query(100, gt=0, le=1000)
):
    """
    Get machine calibration notifications with optional date filtering.
    If no dates are provided, returns the most recent notifications.
    """
    try:
        with db_session:
            query = select(n for n in MachineCalibrationLog)

            # Apply date filters if provided
            if from_date and to_date:
                query = query.filter(lambda n: n.timestamp.date() >= from_date and n.timestamp.date() <= to_date)
            elif from_date:
                query = query.filter(lambda n: n.timestamp.date() >= from_date)
            elif to_date:
                query = query.filter(lambda n: n.timestamp.date() <= to_date)

            # Order by timestamp descending (newest first)
            query = query.order_by(lambda n: desc(n.timestamp))

            # Apply limit
            notifications = list(query.limit(limit))

            notification_dicts = []
            for n in notifications:
                # Get machine details
                machine_id = None
                machine_name = None
                machine_type = None
                machine_make = None

                if n.machine_id:
                    try:
                        from app.models.master_order import Machine
                        machine = Machine.get(id=n.machine_id.id if hasattr(n.machine_id, 'id') else n.machine_id)
                        if machine:
                            machine_id = machine.id
                            machine_type = machine.type
                            machine_make = machine.make
                            machine_name = f"{machine.make} {machine.model}"
                    except Exception as e:
                        print(f"Error fetching machine details: {str(e)}")
                        machine_id = n.machine_id.id if hasattr(n.machine_id, 'id') else n.machine_id

                notification_dict = {
                    "id": n.id,
                    "timestamp": n.timestamp.isoformat(),
                    "calibration_due_date": n.calibration_due_date.isoformat() if n.calibration_due_date else None,
                    "machine_id": machine_id,
                    "machine_name": machine_name,
                    "machine_type": machine_type,
                    "machine_make": machine_make
                }
                notification_dicts.append(notification_dict)

            return {
                "total_notifications": len(notification_dicts),
                "notifications": notification_dicts
            }
    except Exception as e:
        print(f"Error fetching machine calibration notifications: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error fetching notifications: {str(e)}")


# REST endpoint to get instrument calibration notifications
@router.get("/instrument-calibrations")
async def get_instrument_calibration_notifications(
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        limit: int = Query(100, gt=0, le=1000)
):
    """
    Get instrument calibration notifications with optional date filtering.
    If no dates are provided, returns the most recent notifications.
    """
    try:
        with db_session:
            query = select(n for n in InstrumentCalibrationLog)

            # Apply date filters if provided
            if from_date and to_date:
                query = query.filter(lambda n: n.timestamp.date() >= from_date and n.timestamp.date() <= to_date)
            elif from_date:
                query = query.filter(lambda n: n.timestamp.date() >= from_date)
            elif to_date:
                query = query.filter(lambda n: n.timestamp.date() <= to_date)

            # Order by timestamp descending (newest first)
            query = query.order_by(lambda n: desc(n.timestamp))

            # Apply limit
            notifications = list(query.limit(limit))

            notification_dicts = []
            for n in notifications:
                # Get instrument details
                instrument_id = None
                item_name = None
                last_calibration = None
                next_calibration = None
                calibration_type = None
                trade_name = None
                bel_part_number = None

                if n.instrument_id:
                    try:
                        from app.models.inventoryv1 import CalibrationSchedule, InventoryItem
                        calib_schedule = CalibrationSchedule.get(
                            id=n.instrument_id.id if hasattr(n.instrument_id, 'id') else n.instrument_id)
                        if calib_schedule:
                            instrument_id = calib_schedule.id
                            last_calibration = calib_schedule.last_calibration.isoformat() if calib_schedule.last_calibration else None
                            next_calibration = calib_schedule.next_calibration.isoformat() if calib_schedule.next_calibration else None
                            calibration_type = calib_schedule.calibration_type

                            # Get item name from inventory item
                            if calib_schedule.inventory_item:
                                inv_item = calib_schedule.inventory_item
                                item_name = inv_item.item_code

                                # Extract additional details from dynamic_data
                                if hasattr(inv_item, 'dynamic_data') and inv_item.dynamic_data:
                                    try:
                                        dynamic_data = inv_item.dynamic_data
                                        if isinstance(dynamic_data, dict):
                                            # Try to get name from dynamic data
                                            if 'name' in dynamic_data:
                                                item_name = dynamic_data['name']

                                            # Get Trade Name
                                            if 'Trade Name' in dynamic_data:
                                                trade_name = dynamic_data['Trade Name']

                                            # Get BEL Part Number (note the space at the end in the key)
                                            if 'BEL Part Number ' in dynamic_data:
                                                bel_part_number = dynamic_data['BEL Part Number ']
                                            elif 'BEL Part Number' in dynamic_data:  # Try without space too
                                                bel_part_number = dynamic_data['BEL Part Number']
                                    except Exception as e:
                                        print(f"Error parsing dynamic_data for instrument {inv_item.id}: {str(e)}")
                    except Exception as e:
                        print(f"Error fetching instrument details: {str(e)}")
                        instrument_id = n.instrument_id.id if hasattr(n.instrument_id, 'id') else n.instrument_id

                notification_dict = {
                    "id": n.id,
                    "timestamp": n.timestamp.isoformat(),
                    "calibration_due_date": n.calibration_due_date.isoformat() if n.calibration_due_date else None,
                    "instrument_id": instrument_id,
                    "item_name": item_name,
                    "last_calibration": last_calibration,
                    "next_calibration": next_calibration,
                    "calibration_type": calibration_type,
                    "trade_name": trade_name,
                    "bel_part_number": bel_part_number
                }
                notification_dicts.append(notification_dict)

            return {
                "total_notifications": len(notification_dicts),
                "notifications": notification_dicts
            }
    except Exception as e:
        print(f"Error fetching instrument calibration notifications: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error fetching notifications: {str(e)}")