import traceback

from dateutil import parser
from fastapi import APIRouter, HTTPException, Query, Depends, BackgroundTasks
from pony.orm import db_session, select, commit, desc
from typing import Dict, Optional, List, Set, Any
from datetime import datetime, timedelta

from pydantic import BaseModel

from app.core.security import get_current_user
from app.models.production import OEEIssue
from app.schemas.comp_maintainance import (
    MachineStatusResponse, MachineStatusOut, UpdateMachineStatusRequest, IssueIn,
)
from app.models import MachineStatus, Status, ProductionLog, ScheduleVersion, PlannedScheduleItem, Machine, Operation, \
    Order
from app.models.logs import MachineStatusLog, RawMaterialStatusLog
from app.schemas.scheduled1 import ScheduledOperation
from .notification_service import send_notification
from .scheduled import schedule

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


# @router.get("/machines/{machine_id}/operations", response_model=Dict[str, Any])
# @db_session
# def get_machine_operations(
#         machine_id: int,
# ):
#     """
#     Get machine details, operation status information, and order details for a specific machine.
#     This endpoint only returns information for part numbers that have operations in progress.
#
#     Operations are categorized as:
#     - completed: Operations where planned quantity has been completed before the current time
#     - inprogress: Operations running their quantity in the current time
#     - scheduled: Operations not yet started their quantity in the current time
#     """
#     # Debug variables to track where the error happens
#     debug_step = "start"
#
#     try:
#         print(f"Starting get_machine_operations for machine_id={machine_id}")
#         debug_step = "machine_check"
#
#         # Check if machine exists
#         machine = Machine.get(id=machine_id)
#         if not machine:
#             raise HTTPException(
#                 status_code=404,
#                 detail=f"Machine with ID {machine_id} not found"
#             )
#
#         print(f"Found machine: ID={machine_id}, Type={machine.type}, Make={machine.make}")
#         debug_step = "machine_details"
#
#         # Get machine details
#         machine_details = {
#             "id": machine.id,
#             "type": machine.type,
#             "make": machine.make,
#             "model": machine.model,
#             "cnc_controller": machine.cnc_controller if machine.cnc_controller else "",
#             "work_center": {
#                 "id": machine.work_center.id,
#                 "code": machine.work_center.code,
#                 "name": machine.work_center.work_center_name if machine.work_center.work_center_name else "",
#                 "is_schedulable": machine.work_center.is_schedulable if machine.work_center else False
#             } if machine.work_center else None
#         }
#
#         print("Machine details created successfully")
#         debug_step = "get_operations"
#
#         # Current time for status determination
#         now = datetime.utcnow()
#         print(f"Current time for operation status determination: {now.isoformat()}")
#
#         # First, get all unique operations for this machine
#         operations_query = select((o.id, o.operation_number, o.operation_description, o.setup_time, o.ideal_cycle_time,
#                                   o.order.id, o.order.production_order, o.order.part_number, o.order.part_description)
#                                  for o in Operation if o.machine.id == machine_id)
#
#         operations_data = []
#         try:
#             operations_data = list(operations_query)
#             print(f"Found {len(operations_data)} unique operations for machine")
#         except Exception as op_error:
#             print(f"Error retrieving operations: {str(op_error)}")
#             operations_data = []
#
#         # Initialize response structure
#         operations_response = {
#             "completed": [],
#             "inprogress": [],
#             "scheduled": []
#         }
#
#         # Dictionary to store order details to avoid duplicate queries
#         order_details_cache = {}
#
#         # Dictionary to track in-progress status for each order
#         order_inprogress_status = {}
#
#         # STEP 1: First collect all operations with their schedule info
#         all_operations = []
#
#         # STEP 1.1: Get all schedule versions regardless of operation - to find the latest end date
#         all_schedule_versions = []
#
#         for op_data in operations_data:
#             op_id, op_number, op_description, setup_time, cycle_time, order_id, production_order, part_number, part_description = op_data
#
#             try:
#                 # Get the corresponding planned schedule item
#                 schedule_item_query = select(psi for psi in PlannedScheduleItem
#                                             if psi.operation.id == op_id
#                                             and psi.machine.id == machine_id)
#
#                 schedule_items = list(schedule_item_query)
#                 if not schedule_items:
#                     print(f"No planned schedule items found for operation {op_id}")
#                     continue
#
#                 schedule_item = schedule_items[0]  # Should be only one item per operation-machine combination
#
#                 # Find the active schedule version for this schedule item
#                 schedule_version_query = select(sv for sv in ScheduleVersion
#                                                if sv.schedule_item.id == schedule_item.id
#                                                and sv.is_active == True)
#
#                 schedule_versions = list(schedule_version_query)
#
#                 if not schedule_versions:
#                     print(f"No active schedule versions found for operation {op_id}")
#                     continue
#
#                 # Use the latest version if multiple exist
#                 current_version = max(schedule_versions, key=lambda sv: sv.version_number)
#
#                 # Get start time
#                 planned_start_time = None
#                 if hasattr(current_version, 'planned_start_time') and current_version.planned_start_time is not None:
#                     planned_start_time = current_version.planned_start_time
#
#                 if not planned_start_time:
#                     print(f"No start time found for operation {op_id}")
#                     continue
#
#                 # Get end time for reference
#                 planned_end_time = None
#                 if hasattr(current_version, 'planned_end_time') and current_version.planned_end_time is not None:
#                     planned_end_time = current_version.planned_end_time
#
#                 # Get other operation data
#                 planned_quantity = 0
#                 if hasattr(current_version, 'planned_quantity') and current_version.planned_quantity is not None:
#                     planned_quantity = int(current_version.planned_quantity)
#
#                 remaining_quantity = 0
#                 if hasattr(current_version, 'remaining_quantity') and current_version.remaining_quantity is not None:
#                     remaining_quantity = int(current_version.remaining_quantity)
#
#                 # Get completed quantity from production logs
#                 completed_quantity = 0
#                 try:
#                     production_logs = list(select(pl for pl in ProductionLog if pl.schedule_version == current_version))
#                     for pl in production_logs:
#                         if hasattr(pl, 'quantity_completed') and pl.quantity_completed is not None:
#                             completed_quantity += int(pl.quantity_completed)
#                 except Exception as logs_error:
#                     print(f"Error getting production logs: {str(logs_error)}")
#
#                 # Get order details if not already cached
#                 if order_id not in order_details_cache:
#                     order = Order.get(id=order_id)
#                     if order:
#                         # Get project details for delivery date
#                         project = order.project
#
#                         order_details_cache[order_id] = {
#                             "order_id": order.id,
#                             "priority": project.priority if project else 1,
#                             "part_number": order.part_number,
#                             "production_order": order.production_order,
#                             "material_description": order.part_description or "",
#                             "required_qty": order.required_quantity,
#                             "launched_qty": order.launched_quantity,
#                             "sales_order": order.sale_order or "",
#                             "wbs_element": order.wbs_element or "",
#                             "full_description": f"Sale order :{order.sale_order or 'N/A'} Part Desc :{order.part_description or 'N/A'} Tot.No of Oprns :{order.total_operations}",
#                             "project_details": {
#                                 "total_operations": order.total_operations,
#                                 "project_name": project.name if project else "",
#                                 # "delivery_date": project.delivery_date.strftime("%d %b %Y") if project and hasattr(
#                                 #     project, "delivery_date") else ""
#                             },
#                             "has_inprogress": False  # Initialize as False
#                         }
#
#                     # Initialize in-progress status for this order
#                     order_inprogress_status[order_id] = False
#
#                 # Get all schedule versions for this operation to find potential later versions
#                 all_versions_query = select(sv for sv in ScheduleVersion
#                                             if sv.schedule_item.operation.id == op_id
#                                             and sv.is_active == True)
#
#                 all_versions = list(all_versions_query)
#
#                 # Add to our collection of all schedule versions for final date verification
#                 for version in all_versions:
#                     if hasattr(version, 'planned_end_time') and version.planned_end_time is not None:
#                         all_schedule_versions.append({
#                             "op_id": op_id,
#                             "op_number": op_number,
#                             "planned_end_time": version.planned_end_time,
#                             "version_id": version.id
#                         })
#
#                 # Add to our list of operations
#                 all_operations.append({
#                     "op_id": op_id,
#                     "op_number": op_number,
#                     "op_description": op_description or "",
#                     "setup_time": float(setup_time) if setup_time else 0,
#                     "cycle_time": float(cycle_time) if cycle_time else 0,
#                     "total_processing_time": float(setup_time) + float(
#                         cycle_time) if setup_time and cycle_time else float(setup_time or cycle_time or 0),
#                     "order_id": order_id,
#                     "production_order": production_order,
#                     "part_number": part_number,
#                     "part_description": part_description or "",
#                     "planned_start_time": planned_start_time,
#                     "planned_end_time": planned_end_time,  # Store original end time for reference
#                     "planned_quantity": planned_quantity,
#                     "completed_quantity": completed_quantity,
#                     "remaining_quantity": remaining_quantity,
#                     "version": current_version
#                 })
#
#             except Exception as op_error:
#                 print(f"Error processing operation {op_id} in first pass: {str(op_error)}")
#                 continue
#
#         # STEP 2: Find the global latest end date from all schedule versions
#         latest_end_time = None
#         if all_schedule_versions:
#             latest_schedule = max(all_schedule_versions, key=lambda x: x["planned_end_time"])
#             latest_end_time = latest_schedule["planned_end_time"]
#             print(
#                 f"Latest schedule end time across all operations: {latest_end_time} (Operation {latest_schedule['op_id']} #{latest_schedule['op_number']}, Version {latest_schedule['version_id']})")
#
#         # STEP 3: Sort operations by start time
#         all_operations.sort(key=lambda x: x["planned_start_time"])
#
#         # STEP 4: Now process operations in order and explicitly set end times
#         for i, operation in enumerate(all_operations):
#             op_id = operation["op_id"]
#             op_number = operation["op_number"]
#             order_id = operation["order_id"]
#
#             # Determine end time
#             planned_end_time = None
#
#             # If there's a next operation, use its start time as this operation's end time
#             if i < len(all_operations) - 1:
#                 next_op = all_operations[i + 1]
#                 planned_end_time = next_op["planned_start_time"]
#                 print(
#                     f"Operation {op_id} (#{op_number}) ends at {planned_end_time} (start of operation {next_op['op_id']} #{next_op['op_number']})")
#             else:
#                 # This is the last operation, use the global latest end time if it's later than this operation's end time
#                 if operation["planned_end_time"] and latest_end_time and latest_end_time > operation[
#                     "planned_end_time"]:
#                     planned_end_time = latest_end_time
#                     print(f"Using global latest end time for last operation {op_id}: {planned_end_time}")
#                 else:
#                     # Use this operation's end time
#                     planned_end_time = operation["planned_end_time"]
#                     print(f"Using operation's own end time for last operation {op_id}: {planned_end_time}")
#
#                 # If still no end time, calculate one
#                 if not planned_end_time:
#                     # Calculate end time
#                     total_hours = operation["setup_time"] + (operation["cycle_time"] * operation["planned_quantity"])
#                     planned_end_time = operation["planned_start_time"] + timedelta(hours=total_hours)
#                     print(f"Calculated end time for last operation {op_id}: {planned_end_time}")
#
#             # Determine status
#             status = "scheduled"  # Default status
#
#             # If no end time, default to scheduled
#             if planned_end_time is None:
#                 print(f"Missing end time for operation {op_id}, defaulting to scheduled")
#                 status = "scheduled"
#             else:
#                 # 1. COMPLETED: Fully completed quantity before current time
#                 if operation["planned_quantity"] > 0 and operation["completed_quantity"] >= operation[
#                     "planned_quantity"] and now > operation["planned_start_time"]:
#                     status = "completed"
#                     print(f"Operation {op_id} is COMPLETED (quantity completed)")
#                 # 2. IN PROGRESS: Operation is running now (within time window & not completed)
#                 elif operation["planned_start_time"] <= now <= planned_end_time and operation["completed_quantity"] < \
#                         operation["planned_quantity"]:
#                     status = "inprogress"
#                     print(f"Operation {op_id} is IN PROGRESS (in time window)")
#
#                     # Update the order's in-progress status
#                     order_inprogress_status[order_id] = True
#
#                 # 3. SCHEDULED: Not yet started (before start time)
#                 elif now < operation["planned_start_time"]:
#                     status = "scheduled"
#                     print(f"Operation {op_id} is SCHEDULED (before start time)")
#                 # 4. Past end time but not completed
#                 elif now > planned_end_time:
#                     status = "completed"
#                     print(f"Operation {op_id} is COMPLETED (past end time)")
#
#             # Create final operation data with the determined end time
#             operation_data = {
#                 "operation_id": op_id,
#                 "operation_number": op_number,
#                 "description": operation["op_description"],
#                 "order_id": operation["order_id"],
#                 "production_order": operation["production_order"],
#                 "part_number": operation["part_number"],
#                 "part_description": operation["part_description"],
#                 "schedule_info": {
#                     "planned_start_time": operation["planned_start_time"].isoformat(),
#                     "planned_end_time": planned_end_time.isoformat() if planned_end_time else None,
#                     "is_schedulable": machine.work_center.is_schedulable if machine.work_center else False
#                     # "planned_quantity": operation["planned_quantity"],
#                     # "completed_quantity": operation["completed_quantity"],
#                     # "remaining_quantity": operation["remaining_quantity"],
#                     # "setup_time": operation["setup_time"],
#                     # "cycle_time": operation["cycle_time"],
#                     # "total_processing_time": operation["total_processing_time"]
#                 },
#                 # "order_details": order_details_cache.get(operation["order_id"], {})
#             }
#
#             # Add to appropriate category
#             operations_response[status].append(operation_data)
#             print(f"Successfully added operation {op_id} to {status} category")
#
#         # Update all order records with their in-progress status
#         for order_id, has_inprogress in order_inprogress_status.items():
#             if order_id in order_details_cache:
#                 order_details_cache[order_id]["has_inprogress"] = has_inprogress
#
#         # Sort each category by planned start time
#         for status_key in operations_response:
#             try:
#                 operations_response[status_key] = sorted(
#                     operations_response[status_key],
#                     key=lambda x: (x.get("schedule_info", {}).get("planned_start_time") or "9999-12-31")
#                 )
#                 print(f"Successfully sorted {len(operations_response[status_key])} operations in {status_key} category")
#             except Exception as sort_error:
#                 print(f"Error sorting operations for status {status_key}: {str(sort_error)}")
#
#         # Check if there are any operations in progress
#         global_has_inprogress = len(operations_response["inprogress"]) > 0
#
#         # Define part_numbers_in_progress before using it
#         part_numbers_in_progress = set()
#         if global_has_inprogress:
#             # Get the part numbers in progress
#             part_numbers_in_progress = set([op["part_number"] for op in operations_response["inprogress"]])
#             print(
#                 f"Found {len(part_numbers_in_progress)} part numbers in progress: {', '.join(part_numbers_in_progress)}")
#
#         # If no operations in progress, return all operations (don't filter)
#         if not global_has_inprogress:
#             response = {
#                 "machine": machine_details,
#                 "operations": operations_response,
#                 "orders": list(order_details_cache.values()),
#                 "totals": {
#                     "completed": len(operations_response["completed"]),
#                     "inprogress": len(operations_response["inprogress"]),
#                     "scheduled": len(operations_response["scheduled"])
#                 }
#             }
#             print("No operations in progress - returning all operations")
#             return response
#
#         # Get all unique part numbers
#         all_part_numbers = set()
#         for status in operations_response:
#             for op in operations_response[status]:
#                 all_part_numbers.add(op["part_number"])
#
#         # Only filter if there are multiple part numbers AND at least one has in-progress operations
#         if len(all_part_numbers) > 1 and global_has_inprogress:
#             print(
#                 f"Multiple part numbers found ({len(all_part_numbers)}), filtering to only show in-progress part numbers")
#
#             # Filter all operations to only include those with part numbers that are in progress
#             filtered_operations = {
#                 "completed": [],
#                 "inprogress": [],
#                 "scheduled": []
#             }
#
#             # Filter operations to only keep those with in-progress part numbers
#             for status in operations_response:
#                 for op in operations_response[status]:
#                     if op["part_number"] in part_numbers_in_progress:
#                         filtered_operations[status].append(op)
#         else:
#             # If only one part number or no in-progress operations, don't filter
#             filtered_operations = operations_response
#             print("Not filtering operations - only one part number or no in-progress operations")
#
#         # Filter orders to only include those with in-progress part numbers
#         filtered_orders = [
#             order for order in order_details_cache.values()
#             if order["part_number"] in part_numbers_in_progress
#         ] if global_has_inprogress else list(order_details_cache.values())
#
#         # Build complete response with only the in-progress part numbers
#         response = {
#             "machine": machine_details,
#             "operations": filtered_operations,
#             "orders": filtered_orders,
#             "totals": {
#                 "completed": len(filtered_operations["completed"]),
#                 "inprogress": len(filtered_operations["inprogress"]),
#                 "scheduled": len(filtered_operations["scheduled"])
#             }
#         }
#
#         print("Response built successfully - filtered to only include in-progress part numbers")
#         return response
#
#     except Exception as e:
#         print(f"ERROR in get_machine_operations at step {debug_step}: {str(e)}")
#         traceback.print_exc()
#         raise HTTPException(
#             status_code=500,
#             detail=f"Error retrieving machine operations at step {debug_step}: {str(e)}"
#         )


try:
    # Python 3.9+
    from zoneinfo import ZoneInfo
except ImportError:
    # For older Python versions, install backports.zoneinfo or fallback to pytz
    import pytz
    ZoneInfo = None

IST = ZoneInfo("Asia/Kolkata") if ZoneInfo else pytz.timezone("Asia/Kolkata")


class MachineScheduleResponse(BaseModel):
    machine: dict
    operations: Dict[str, List[dict]]
    orders: List[dict]
    totals: dict


def normalize_datetime_to_ist(dt_input):
    """
    Utility function to normalize datetime input to IST timezone.
    Handles both string and datetime objects.
    """
    if isinstance(dt_input, str):
        dt = parser.isoparse(dt_input)
    else:
        dt = dt_input

    if dt.tzinfo is None:
        # Assume IST if no timezone info
        return dt.replace(tzinfo=IST)
    else:
        # Convert to IST if timezone info exists
        return dt.astimezone(IST)


# Alternative version with the utility function
@router.get("/machines/{machine_id}/operations", response_model=MachineScheduleResponse)
async def get_machine_schedule_v2(machine_id: int):
    """
    Get machine details, operation status information, and order details for a specific machine.
    This endpoint only returns information for part numbers that have operations in progress.

    Operations are categorized as:
    - completed: Operations where planned quantity has been completed before the current time
    - inprogress: Operations running their quantity in the current time
    - scheduled: Operations not yet started their quantity in the current time
    """
    try:
        debug_step = "machine_lookup"

        with db_session:
            machine = Machine.get(id=machine_id)
            if not machine:
                raise HTTPException(status_code=404, detail=f"Machine with ID {machine_id} not found")

            # Get machine details - matching first endpoint structure
            machine_details = {
                "id": machine.id,
                "type": machine.type,
                "make": machine.make,
                "model": machine.model,
                "cnc_controller": machine.cnc_controller or "",
                "work_center": {
                    "id": machine.work_center.id,
                    "code": machine.work_center.code,
                    "name": machine.work_center.work_center_name or "",
                    "is_schedulable": machine.work_center.is_schedulable,
                } if machine.work_center else None,
            }

            machine_name = f"{machine.work_center.code}-{machine.make}" if machine.work_center else f"Machine-{machine.id}"

        debug_step = "schedule_fetch"
        schedule_response = await schedule()

        debug_step = "operations_filtering"
        machine_operations = [
            op for op in schedule_response.scheduled_operations if machine_name in op.machine
        ]

        # Get current time in IST timezone
        current_time = datetime.now(IST)

        # Initialize response structure - matching first endpoint
        operations_response = {
            "completed": [],
            "inprogress": [],
            "scheduled": []
        }

        # Dictionary to store order details and track in-progress status
        order_details_cache = {}
        order_inprogress_status = {}
        part_numbers_in_progress = set()

        debug_step = "categorization"

        for operation in machine_operations:
            try:
                # Normalize datetimes to IST
                start_time = normalize_datetime_to_ist(operation.start_time)
                end_time = normalize_datetime_to_ist(operation.end_time)

                # Determine status
                if end_time <= current_time:
                    status = "completed"
                elif start_time <= current_time < end_time:
                    status = "inprogress"
                    # Track part numbers in progress
                    if hasattr(operation, 'part_number'):
                        part_numbers_in_progress.add(operation.part_number)
                else:
                    status = "scheduled"

                # Create operation data structure matching first endpoint
                operation_data = {
                    "operation_id": getattr(operation, 'operation_id', None),
                    "operation_number": getattr(operation, 'operation_number', ''),
                    "description": operation.description,
                    "order_id": getattr(operation, 'order_id', None),
                    "production_order": getattr(operation, 'production_order', ''),
                    "part_number": getattr(operation, 'part_number', ''),
                    "part_description": getattr(operation, 'part_description', ''),
                    "schedule_info": {
                        "planned_start_time": start_time.isoformat(),
                        "planned_end_time": end_time.isoformat(),
                        "is_schedulable": machine.work_center.is_schedulable if machine.work_center else False
                    }
                }

                # Add to appropriate category
                operations_response[status].append(operation_data)

                # Cache order details if available
                if hasattr(operation, 'order_id') and operation.order_id:
                    order_id = operation.order_id
                    if order_id not in order_details_cache:
                        # Create order details structure matching first endpoint
                        order_details_cache[order_id] = {
                            "order_id": order_id,
                            "priority": getattr(operation, 'priority', 1),
                            "part_number": getattr(operation, 'part_number', ''),
                            "production_order": getattr(operation, 'production_order', ''),
                            "material_description": getattr(operation, 'part_description', ''),
                            "required_qty": getattr(operation, 'required_qty', 0),
                            "launched_qty": getattr(operation, 'launched_qty', 0),
                            "sales_order": getattr(operation, 'sales_order', ''),
                            "wbs_element": getattr(operation, 'wbs_element', ''),
                            "full_description": f"Sale order :{getattr(operation, 'sales_order', 'N/A')} Part Desc :{getattr(operation, 'part_description', 'N/A')} Tot.No of Oprns :{getattr(operation, 'total_operations', 'N/A')}",
                            "project_details": {
                                "total_operations": getattr(operation, 'total_operations', 0),
                                "project_name": getattr(operation, 'project_name', ''),
                            },
                            "has_inprogress": status == "inprogress"
                        }

                    # Update in-progress status
                    if status == "inprogress":
                        order_inprogress_status[order_id] = True
                        order_details_cache[order_id]["has_inprogress"] = True

            except Exception as e:
                print(f"Error processing operation {operation.description}: {str(e)}")
                # Add to scheduled as fallback
                operations_response["scheduled"].append({
                    "operation_id": None,
                    "operation_number": '',
                    "description": operation.description,
                    "order_id": None,
                    "production_order": '',
                    "part_number": '',
                    "part_description": '',
                    "schedule_info": {
                        "planned_start_time": None,
                        "planned_end_time": None,
                        "is_schedulable": False
                    }
                })
                continue

        # Sort operations by planned start time (matching first endpoint)
        for status_key in operations_response:
            try:
                operations_response[status_key] = sorted(
                    operations_response[status_key],
                    key=lambda x: (x.get("schedule_info", {}).get("planned_start_time") or "9999-12-31")
                )
            except Exception as sort_error:
                print(f"Error sorting operations for status {status_key}: {str(sort_error)}")

        # Check if there are any operations in progress
        global_has_inprogress = len(operations_response["inprogress"]) > 0

        # Filter logic matching first endpoint
        if not global_has_inprogress:
            # No operations in progress - return all operations
            filtered_operations = operations_response
            filtered_orders = list(order_details_cache.values())
        else:
            # Get all unique part numbers
            all_part_numbers = set()
            for status in operations_response:
                for op in operations_response[status]:
                    if op["part_number"]:
                        all_part_numbers.add(op["part_number"])

            # Only filter if there are multiple part numbers AND at least one has in-progress operations
            if len(all_part_numbers) > 1 and global_has_inprogress:
                # Filter operations to only include those with part numbers that are in progress
                filtered_operations = {
                    "completed": [],
                    "inprogress": [],
                    "scheduled": []
                }

                for status in operations_response:
                    for op in operations_response[status]:
                        if op["part_number"] in part_numbers_in_progress:
                            filtered_operations[status].append(op)

                # Filter orders to only include those with in-progress part numbers
                filtered_orders = [
                    order for order in order_details_cache.values()
                    if order["part_number"] in part_numbers_in_progress
                ]
            else:
                # Don't filter if only one part number or no in-progress operations
                filtered_operations = operations_response
                filtered_orders = list(order_details_cache.values())

        # Build response matching first endpoint structure
        response = {
            "machine": machine_details,
            "operations": filtered_operations,
            "orders": filtered_orders,
            "totals": {
                "completed": len(filtered_operations["completed"]),
                "inprogress": len(filtered_operations["inprogress"]),
                "scheduled": len(filtered_operations["scheduled"])
            }
        }

        return response

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error at step {debug_step}: {str(e)}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal server error at step {debug_step}: {str(e)}")

# Function to asynchronously send notifications
async def send_machine_notification(machine_id, machine_make, status_name, description, created_by):
    """Send a machine notification with direct parameters instead of database entity"""
    try:
        with db_session:
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

# Example endpoint for operator to update machine status
@router.post("/machine-status/{machine_id}")
async def update_machine_status(
    machine_id: int,
    status_data: Dict[str, Any],
    background_tasks: BackgroundTasks
):
    """
    Update machine status and send notification to supervisors
    """
    try:
        with db_session:
            # Here you would update your machine status in the main database...

            # Get values from status_data
            machine_make = status_data.get("machine_make", "Unknown")
            status_name = status_data.get("status_name", "Unknown")
            description = status_data.get("description", "")
            created_by = status_data.get("created_by")
            current_time = datetime.now()

            # Then create a notification log
            log_entry = MachineStatusLog(
                machine_id=machine_id,
                machine_make=machine_make,
                status_name=status_name,
                description=description,
                updated_at=current_time,
                created_by=created_by,
                is_acknowledged=False
            )
            # Get the ID for logging
            log_id = log_entry.id
            commit()

            print(f"Created machine notification log with ID {log_id}")

            # Add task to send notification asynchronously
            background_tasks.add_task(
                send_machine_notification,
                machine_id,
                machine_make,
                status_name,
                description,
                created_by
            )

            return {
                "status": "success",
                "message": "Machine status updated and notification sent",
                "notification_id": log_id
            }

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Error updating machine status: {str(e)}"
        )

# Example endpoint for operator to update raw material status
@router.post("/material-status/{material_id}")
async def update_material_status(
    material_id: int,
    status_data: Dict[str, Any],
    background_tasks: BackgroundTasks
):
    """
    Update raw material status and send notification to supervisors
    """
    try:
        with db_session:
            # Here you would update your material status in the main database...

            # Get values from status_data
            part_number = status_data.get("part_number")
            status_name = status_data.get("status_name", "Unknown")
            description = status_data.get("description", "")
            created_by = status_data.get("created_by")
            current_time = datetime.now()

            # Then create a notification log
            log_entry = RawMaterialStatusLog(
                material_id=material_id,
                part_number=part_number,
                status_name=status_name,
                description=description,
                updated_at=current_time,
                created_by=created_by,
                is_acknowledged=False
            )
            # Get the ID for logging
            log_id = log_entry.id
            commit()

            print(f"Created material notification log with ID {log_id}")

            # Add task to send notification asynchronously
            background_tasks.add_task(
                send_material_notification,
                material_id,
                part_number,
                status_name,
                description,
                created_by
            )

            return {
                "status": "success",
                "message": "Material status updated and notification sent",
                "notification_id": log_id
            }

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Error updating material status: {str(e)}"
        )


@router.post("/issues/")
@db_session
def create_issue(issue: IssueIn):
    try:
        new_issue = OEEIssue(
            category=issue.category,
            description=issue.description,
            machine=issue.machine,
            reported_by=issue.reported_by
            # timestamp is automatically added
        )
        return {
            "message": "Issue created successfully",
            "timestamp": new_issue.timestamp.isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))