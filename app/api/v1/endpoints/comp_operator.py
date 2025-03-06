import traceback

from fastapi import APIRouter, HTTPException, Query, Depends, status
from pony.orm import db_session, select
from typing import Dict, Optional, List, Set, Any
from datetime import datetime

from app.core.security import get_current_user
from app.schemas.comp_maintainance import (
    MachineStatusResponse, MachineStatusOut, UpdateMachineStatusRequest,
)
from app.models import MachineStatus, Status, Machine, PlannedScheduleItem, ScheduleVersion, ProductionLog

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



########################################################################################################## app/api/v1/endpoints/comp_operator.py

@router.get("/machines/{machine_id}/operations", response_model=Dict[str, Any])
@db_session
def get_machine_operations(
    machine_id: int,
    current_user: Any = Depends(get_current_user),
):
    """
    Get machine details and operation status information for a specific machine.

    Operations are categorized as:
    - completed: Operations that have been fully completed
    - inprogress: Operations currently running
    - scheduled: Operations scheduled for the future
    """
    # Debug variables to track where the error happens
    debug_step = "start"

    try:
        print(f"Starting get_machine_operations for machine_id={machine_id}")
        debug_step = "machine_check"

        # Check if machine exists
        machine = Machine.get(id=machine_id)
        if not machine:
            raise HTTPException(
                status_code=404,
                detail=f"Machine with ID {machine_id} not found"
            )

        print(f"Found machine: ID={machine_id}, Type={machine.type}, Make={machine.make}")
        debug_step = "machine_details"

        # Get machine details
        machine_details = {
            "id": machine.id,
            "type": machine.type,
            "make": machine.make,
            "model": machine.model,
            "cnc_controller": machine.cnc_controller if machine.cnc_controller else "",
            "work_center": {
                "id": machine.work_center.id,
                "code": machine.work_center.code,
                "name": machine.work_center.work_center_name if machine.work_center.work_center_name else ""
            } if machine.work_center else None
        }

        print("Machine details created successfully")
        debug_step = "get_schedule_items"

        # Current time for status determination
        now = datetime.utcnow()

        # Get all planned schedule items for this machine
        schedule_items_query = select(psi for psi in PlannedScheduleItem if psi.machine.id == machine_id)
        schedule_items = []

        # Safely convert to list with error handling
        try:
            schedule_items = list(schedule_items_query)
            print(f"Found {len(schedule_items)} schedule items for machine")
        except Exception as list_error:
            print(f"Error converting schedule items to list: {str(list_error)}")
            schedule_items = []

        debug_step = "init_operations"

        # Initialize response structure
        operations = {
            "completed": [],
            "inprogress": [],
            "scheduled": []
        }

        debug_step = "process_items"

        # Process each scheduled item to determine its status
        item_count = 0
        for item in schedule_items:
            item_count += 1
            item_debug = f"item_{item_count}"
            print(f"Processing schedule item {item_count} of {len(schedule_items)}")

            try:
                item_debug = f"item_{item_count}_get_version"

                # Get the current active version of this schedule
                current_version = None
                try:
                    current_version = select(sv for sv in ScheduleVersion
                                            if sv.schedule_item == item and sv.is_active == True).first()
                    if current_version:
                        print(f"Found active version for schedule item {item_count}")
                    else:
                        print(f"No active version found for schedule item {item_count}")
                        continue
                except Exception as version_error:
                    print(f"Error getting active version: {str(version_error)}")
                    continue

                item_debug = f"item_{item_count}_get_logs"

                # Get production logs for this schedule version
                production_logs = []
                try:
                    production_logs = list(select(pl for pl in ProductionLog if pl.schedule_version == current_version))
                    print(f"Found {len(production_logs)} production logs for version")
                except Exception as logs_error:
                    print(f"Error getting production logs: {str(logs_error)}")
                    production_logs = []

                item_debug = f"item_{item_count}_calc_completed"

                # Calculate total completed quantity from production logs - safely handle None values
                total_completed = 0
                for pl_index, pl in enumerate(production_logs):
                    print(f"Checking production log {pl_index+1} quantity_completed")

                    # Debug print of the actual value
                    if hasattr(pl, 'quantity_completed'):
                        print(f"quantity_completed value: {pl.quantity_completed} (type: {type(pl.quantity_completed)})")
                    else:
                        print("quantity_completed attribute not found")
                        continue

                    if pl.quantity_completed is None:
                        print("quantity_completed is None, skipping")
                        continue

                    try:
                        # Add with explicit conversion to int
                        total_completed = total_completed + int(pl.quantity_completed)
                        print(f"Added {pl.quantity_completed}, new total: {total_completed}")
                    except (TypeError, ValueError) as add_error:
                        print(f"Error adding quantity: {str(add_error)}")

                item_debug = f"item_{item_count}_determine_status"

                # Determine status based on time and completed quantity
                status = "scheduled"  # Default status

                # Safely handle None values
                planned_quantity = 0
                if hasattr(current_version, 'planned_quantity'):
                    if current_version.planned_quantity is not None:
                        planned_quantity = int(current_version.planned_quantity)
                        print(f"Planned quantity: {planned_quantity}")
                    else:
                        print("Planned quantity is None, using default 0")
                else:
                    print("planned_quantity attribute not found, using default 0")

                # Check status conditions
                if planned_quantity > 0 and total_completed >= planned_quantity:
                    status = "completed"
                    print(f"Status set to COMPLETED (total_completed={total_completed} >= planned_quantity={planned_quantity})")
                elif (hasattr(current_version, 'planned_start_time') and
                      hasattr(current_version, 'planned_end_time') and
                      current_version.planned_start_time is not None and
                      current_version.planned_end_time is not None):

                    if current_version.planned_start_time <= now <= current_version.planned_end_time:
                        status = "inprogress"
                        print(f"Status set to IN PROGRESS (now is between start and end times)")
                    else:
                        print(f"Status remains SCHEDULED (now is not between start and end times)")
                else:
                    print("Missing datetime fields, status remains SCHEDULED")

                item_debug = f"item_{item_count}_get_remaining"

                # Safely get remaining quantity
                remaining_quantity = 0
                if hasattr(current_version, 'remaining_quantity'):
                    if current_version.remaining_quantity is not None:
                        remaining_quantity = int(current_version.remaining_quantity)
                        print(f"Remaining quantity: {remaining_quantity}")
                    else:
                        print("Remaining quantity is None, using default 0")
                else:
                    print("remaining_quantity attribute not found, using default 0")

                item_debug = f"item_{item_count}_build_data"

                # Create operation data object - safely handle all attributes
                operation_data = {
                    "operation_id": item.operation.id,
                    "operation_number": item.operation.operation_number,
                    "description": item.operation.operation_description if hasattr(item.operation, 'operation_description') and item.operation.operation_description else "",
                    "order_id": item.order.id,
                    "production_order": item.order.production_order,
                    "part_number": item.order.part_number,
                    "part_description": item.order.part_description if hasattr(item.order, 'part_description') and item.order.part_description else "",
                    "schedule_info": {
                        "planned_start_time": current_version.planned_start_time.isoformat() if hasattr(current_version, 'planned_start_time') and current_version.planned_start_time else None,
                        "planned_end_time": current_version.planned_end_time.isoformat() if hasattr(current_version, 'planned_end_time') and current_version.planned_end_time else None,
                        "planned_quantity": planned_quantity,
                        "completed_quantity": total_completed,
                        "remaining_quantity": remaining_quantity
                    }
                }

                item_debug = f"item_{item_count}_add_to_response"

                # Add to appropriate category in response
                operations[status].append(operation_data)
                print(f"Successfully added operation to {status} category")

            except Exception as item_error:
                print(f"Error processing schedule item {item_count} at step {item_debug}: {str(item_error)}")
                print(traceback.format_exc())
                # Continue to next item
                continue

        debug_step = "sort_operations"

        # Sort each category by planned start time - with safe handling of None values
        for status_key in operations:
            try:
                operations[status_key] = sorted(
                    operations[status_key],
                    key=lambda x: (x.get("schedule_info", {}).get("planned_start_time") or "9999-12-31")
                )
                print(f"Successfully sorted {len(operations[status_key])} operations in {status_key} category")
            except Exception as sort_error:
                print(f"Error sorting operations for status {status_key}: {str(sort_error)}")
                # Keep original order if sorting fails

        debug_step = "build_response"

        # Build complete response
        response = {
            "machine": machine_details,
            "operations": operations,
            "totals": {
                "completed": len(operations["completed"]),
                "inprogress": len(operations["inprogress"]),
                "scheduled": len(operations["scheduled"])
            }
        }

        print("Response built successfully")
        return response

    except Exception as e:
        print(f"ERROR in get_machine_operations at step {debug_step}: {str(e)}")
        traceback.print_exc()
        # Fix the status code usage - use integer directly
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving machine operations at step {debug_step}: {str(e)}"
        )