from fastapi import APIRouter, HTTPException, Query
from pony.orm import db_session, select
from typing import Dict, Optional, List, Set
from datetime import datetime
from app.schemas.comp_maintainance import (
    MachineStatusResponse, MachineStatusOut, UpdateMachineStatusRequest,
)
from app.models import MachineStatus, Status

# Modified storage to include read status tracking
pending_changes: Dict[int, Dict] = {}
status_messages: Dict[int, List[Dict]] = {}
read_messages: Dict[int, Set[str]] = {}  # Machine ID -> Set of read message timestamps

# Add a dictionary to track read status of messages
message_read_status: Dict[str, bool] = {}


router = APIRouter(prefix="/api/v1/operator", tags=["operator"])


@router.get("/machine-status/", response_model=MachineStatusResponse)
async def get_operator_machine_status():
    """
    Get status information for all machines (Operator view).
    Includes machine make, status, description and availability information.
    """
    try:
        with db_session:
            machine_statuses_raw = list(select(ms for ms in MachineStatus).order_by(lambda ms: ms.machine.id))

            machine_statuses = []
            for ms in machine_statuses_raw:
                machine_status = MachineStatusOut(
                    machine_make=ms.machine.make,
                    status_name=ms.status.name,
                    description=ms.description,  # Added description field
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

@router.put("/machine-status/{machine_id}/request-change")
async def request_machine_status_change(machine_id: int, status_update: UpdateMachineStatusRequest):
    """
    Request a change in machine status (Operator endpoint).
    Changes will be pending until approved by supervisor.
    Includes status, description, and availability updates.
    """
    try:
        with db_session:
            # Verify machine exists
            machine_status = MachineStatus.get(machine=machine_id)
            if not machine_status:
                raise HTTPException(
                    status_code=404,
                    detail=f"Machine status not found for machine ID: {machine_id}"
                )

            # Verify new status exists
            new_status = Status.get(id=status_update.status_id)
            if not new_status:
                raise HTTPException(
                    status_code=404,
                    detail=f"Status with ID {status_update.status_id} not found"
                )

            # Store the change request with description
            pending_changes[machine_id] = {
                "status_id": status_update.status_id,
                "description": status_update.description,  # Added description
                "available_from": status_update.available_from,
                "requested_at": datetime.now(),
                "current_status": {
                    "status_id": machine_status.status.id,
                    "description": machine_status.description,  # Added current description
                    "available_from": machine_status.available_from
                }
            }

            return {
                "message": "Change request submitted for approval",
                "machine_id": machine_id,
                "requested_status": new_status.name,
                "description": status_update.description  # Added to response
            }

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error submitting change request: {str(e)}"
        )

# Update the get-pending-changes endpoint to include description
@router.get("/pending-changes/")
async def get_pending_changes():
    """
    Get all pending machine status changes (Supervisor endpoint)
    """
    try:
        with db_session:
            pending_list = []
            for machine_id, change in pending_changes.items():
                machine_status = MachineStatus.get(machine=machine_id)
                new_status = Status.get(id=change["status_id"])

                pending_list.append({
                    "machine_id": machine_id,
                    "machine_make": machine_status.machine.make,
                    "current_status": machine_status.status.name,
                    "current_description": machine_status.description,  # Added current description
                    "requested_status": new_status.name,
                    "requested_description": change["description"],  # Added requested description
                    "requested_at": change["requested_at"],
                    "available_from": change["available_from"]
                })

            return {
                "total_pending": len(pending_list),
                "pending_changes": pending_list
            }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching pending changes: {str(e)}"
        )

#
# @router.get("/Machine-status-Notification")
# async def get_latest_status_message():
#     """
#     Get the latest status message from the system
#     """
#     try:
#         with db_session:
#             # Get all machine statuses
#             if not status_messages:
#                 return {
#                     "messages": []
#                 }
#
#             # Get the latest messages for all machines
#             latest_messages = {}
#             for machine_id, messages in status_messages.items():
#                 if messages:  # If there are messages for this machine
#                     latest_messages[machine_id] = messages[-1]
#
#             return {
#                 "latest_messages": latest_messages
#             }
#     except Exception as e:
#         raise HTTPException(
#             status_code=500,
#             detail=f"Error fetching latest status messages: {str(e)}"
#         )



@router.post("/approve-change/{machine_id}")
async def approve_status_change(machine_id: int):
    if machine_id not in pending_changes:
        raise HTTPException(
            status_code=404,
            detail="No pending change found for this machine"
        )

    try:
        with db_session:
            change = pending_changes[machine_id]
            machine_status = MachineStatus.get(machine=machine_id)
            new_status = Status.get(id=change["status_id"])

            # Store the approval message
            if machine_id not in status_messages:
                status_messages[machine_id] = []

            status_messages[machine_id].append({
                "type": "approval",
                "timestamp": datetime.now().isoformat(),
                "old_status": machine_status.status.name,
                "new_status": new_status.name,
                "description": change["description"]
            })

            # Update machine status
            machine_status.status = new_status
            machine_status.description = change["description"]
            machine_status.available_from = change["available_from"]

            # Remove the pending change
            del pending_changes[machine_id]

            return {
                "message": "Change approved and implemented",
                "machine_id": machine_id,
                "new_status": new_status.name,
                "description": change["description"]
            }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error approving change: {str(e)}"
        )



@router.post("/reject-change/{machine_id}")
async def reject_status_change(machine_id: int, reason: str = Query(..., description="Reason for rejection")):
    if machine_id not in pending_changes:
        raise HTTPException(
            status_code=404,
            detail="No pending change found for this machine"
        )

    try:
        change = pending_changes[machine_id]

        # Store the rejection message
        if machine_id not in status_messages:
            status_messages[machine_id] = []

        status_messages[machine_id].append({
            "type": "rejection",
            "timestamp": datetime.now().isoformat(),
            "requested_status": Status.get(id=change["status_id"]).name,
            "reason": reason,
            "description": change["description"]
        })

        # Remove the pending change
        del pending_changes[machine_id]

        return {
            "message": "Change request rejected",
            "machine_id": machine_id,
            "reason": reason
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error rejecting change: {str(e)}"
        )


@router.get("/Machine-status-Notification")
async def get_status_messages():
    """
    Get all unread status messages from the system across all machines.
    Also returns messages marked for retention.
    """
    try:
        with db_session:
            if not status_messages:
                return {"messages": []}

            # Collect all unread messages
            unread_messages = []

            for machine_id, messages in status_messages.items():
                for message in messages:
                    msg_id = f"{machine_id}_{message['timestamp']}"

                    # Include message if it's unread or marked for retention
                    if msg_id not in message_read_status or \
                            not message_read_status[msg_id].get("read", False) or \
                            message_read_status[msg_id].get("retain", False):
                        unread_messages.append({
                            "machine_id": machine_id,
                            **message
                        })

            # Sort messages by timestamp (newest first)
            unread_messages.sort(
                key=lambda x: datetime.fromisoformat(x["timestamp"]),
                reverse=True
            )

            return {"messages": unread_messages}

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching status messages: {str(e)}"
        )


@router.put("/Machine-status-Notification/{machine_id}/{timestamp}")
async def update_message_read_status(
        machine_id: int,
        timestamp: str,
        read: bool = True,
        retain: bool = False
):
    """
    Update the read status of a specific message.

    Parameters:
    - machine_id: ID of the machine
    - timestamp: Timestamp of the message
    - read: Boolean indicating if message is read (default: True)
    - retain: Boolean indicating if message should be retained even when read (default: False)
    """
    try:
        # Verify machine and message exist
        if machine_id not in status_messages:
            raise HTTPException(
                status_code=404,
                detail="No messages found for this machine"
            )

        # Find message with matching timestamp
        message_found = False
        for msg in status_messages[machine_id]:
            if msg["timestamp"] == timestamp:
                message_found = True
                break

        if not message_found:
            raise HTTPException(
                status_code=404,
                detail="Message not found"
            )

        # Update read status and retention flag
        msg_id = f"{machine_id}_{timestamp}"
        message_read_status[msg_id] = {
            "read": read,
            "retain": retain
        }

        return {
            "message": "Message status updated successfully",
            "machine_id": machine_id,
            "timestamp": timestamp,
            "read": read,
            "retain": retain
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error updating message status: {str(e)}"
        )