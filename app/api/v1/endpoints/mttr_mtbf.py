from functools import total_ordering
from fastapi import FastAPI, HTTPException, APIRouter
from pony.orm import db_session, select, commit, desc
from datetime import timedelta, datetime
from typing import Dict, List, Optional
from starlette import status
from app.models import Machine
from app.models.production import MachineDowntimes  # Assuming your model is in models.py
from collections import defaultdict
from app.schemas.mttr_mtbf import DowntimeResponse, DowntimeCreate, DowntimeAction


router = APIRouter(prefix="/api/v1/maintainance", tags=["mttr-mtbf"])


@router.get("/metrics/machine-performance")
@db_session
def get_machine_performance_metrics(machine_id: Optional[int] = None):
    """
    Calculate MTTR and MTBF for machines with proper handling of all scenarios.
    """
    # Get all downtimes
    query = select(d for d in MachineDowntimes)

    if machine_id is not None:
        query = query.filter(lambda d: d.machine_id == machine_id)

    all_downtimes = list(query.order_by(MachineDowntimes.machine_id, MachineDowntimes.open_dt))

    if not all_downtimes:
        raise HTTPException(status_code=404, detail="No downtimes found")

    machine_downtimes = defaultdict(list)
    for downtime in all_downtimes:
        machine_downtimes[downtime.machine_id].append(downtime)

    current_time = datetime.now()
    result = {}
    total_repair_time = 0
    total_between_failures_time = 0
    total_failures = 0

    for machine_id, machine_records in machine_downtimes.items():
        # Sort records by open_dt
        machine_records.sort(key=lambda x: x.open_dt)

        # Calculate MTTR from closed downtimes
        closed_downtimes = [d for d in machine_records if d.closed_dt is not None]
        repair_times = [(d.closed_dt - d.open_dt).total_seconds() / 3600 for d in closed_downtimes]
        mttr = sum(repair_times) / len(repair_times) if repair_times else 0

        # Calculate MTBF between any closed downtime and the next opened downtime
        between_failure_times = []

        # Mix closed and all downtimes for MTBF calculation
        for i in range(len(closed_downtimes)):
            current_closed_dt = closed_downtimes[i].closed_dt

            # Find the next downtime (if any) after this closed one
            next_downtime_index = -1
            for j, downtime in enumerate(machine_records):
                if downtime.open_dt > current_closed_dt:
                    next_downtime_index = j
                    break

            # If there is a next downtime, calculate time between
            if next_downtime_index != -1:
                next_open_dt = machine_records[next_downtime_index].open_dt
                between_time = (next_open_dt - current_closed_dt).total_seconds() / 3600
                between_failure_times.append(between_time)

        # Only add current uptime if the last downtime record is closed
        if machine_records and machine_records[-1].closed_dt is not None:
            last_closed_dt = machine_records[-1].closed_dt
            current_uptime = (current_time - last_closed_dt).total_seconds() / 3600
            between_failure_times.append(current_uptime)

        mtbf = sum(between_failure_times) / len(between_failure_times) if between_failure_times else 0

        total_repair_time += sum(repair_times)
        total_between_failures_time += sum(between_failure_times)
        total_failures += len(between_failure_times) if between_failure_times else 0

        machine_obj = Machine.get(id=machine_id)
        machine_name = f"{machine_obj.make}" if machine_obj else "Unknown"

        result[machine_id] = {
            "machine_id": machine_id,
            "machine_name": machine_name,
            "mttr": round(mttr, 2),
            "mtbf": round(mtbf, 2),
            "total_failures": len(machine_records),
            "unit": "hours"
        }

    return {
        "machines": result,
        "mttr_shop": total_repair_time / total_failures,
        "mtbf_shop": total_between_failures_time / total_failures,
        "total_failures": total_failures,
        "timestamp": current_time.isoformat()
    }


@router.get("/metrics/machine-performance/{machine_id}")
@db_session
def get_single_machine_performance(machine_id: int):
    """
    Get MTTR and MTBF metrics for a specific machine
    """
    return get_machine_performance_metrics(machine_id=machine_id)


# Helper function to convert database object to response model
def downtime_to_response(downtime) -> DowntimeResponse:
    status = "open"
    if downtime.closed_dt:
        status = "closed"
    elif downtime.inprogress_dt:
        status = "in_progress"

    machine = Machine.get(id=downtime.machine_id)
    machine_name = machine.make if machine else "Unknown"

    return DowntimeResponse(
        id=downtime.id,
        machine_id=downtime.machine_id,
        machine_name=machine_name,
        category=downtime.category,
        description=downtime.description,
        priority=downtime.priority,
        reported_by=downtime.reported_by,
        open_dt=downtime.open_dt,
        inprogress_dt=downtime.inprogress_dt,
        closed_dt=downtime.closed_dt,
        action_taken=downtime.action_taken,
        status=status
    )


# POST endpoint to create a new machine downtime
@router.post("/downtimes/", response_model=DowntimeResponse, status_code=status.HTTP_201_CREATED)
@db_session
def create_downtime(downtime: DowntimeCreate):
    """
    Create a new machine downtime record with the current timestamp as open_dt.
    Only allow creating a new downtime if there are no open downtimes for the machine.
    """
    # Check if there are any open downtimes for this machine
    existing_open_downtime = select(d for d in MachineDowntimes
                                    if d.machine_id == downtime.machine_id and
                                    d.closed_dt is None).first()

    # If an open downtime exists, return an error
    if existing_open_downtime:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Machine ID {downtime.machine_id} already has an open downtime. "
                   f"Please close the existing downtime before creating a new one."
        )

    # If no open downtime exists, create the new downtime
    new_downtime = MachineDowntimes(
        machine_id=downtime.machine_id,
        category=downtime.category,
        description=downtime.description,
        priority=downtime.priority,
        reported_by=downtime.reported_by,
        open_dt=datetime.now()  # Use current timestamp
    )

    commit()  # Commit to get the ID
    return downtime_to_response(new_downtime)


# GET endpoint for supervisors to view all open/in-progress downtimes
@router.get("/supervisor/downtimes/", response_model=List[DowntimeResponse])
@db_session
def get_active_downtimes(machine_id: Optional[int] = None, status: Optional[str] = None):
    """
    Get all active downtimes (open or in progress) for supervisor review
    """
    query = select(d for d in MachineDowntimes if d.closed_dt is None)

    if machine_id:
        query = query.filter(lambda d: d.machine_id == machine_id)

    if status == "open":
        query = query.filter(lambda d: d.inprogress_dt is None)
    elif status == "in_progress":
        query = query.filter(lambda d: d.inprogress_dt is not None)

    downtimes = list(query.order_by(desc(MachineDowntimes.priority), MachineDowntimes.open_dt))
    return [downtime_to_response(d) for d in downtimes]


# PUT endpoint for supervisor to acknowledge the downtime (set inprogress_dt)
@router.put("/supervisor/downtimes/{downtime_id}/acknowledge", response_model=DowntimeResponse)
@db_session
def acknowledge_downtime(downtime_id: int):
    """
    Acknowledge a downtime by setting the inprogress_dt to the current timestamp
    """
    downtime = MachineDowntimes.get(id=downtime_id)

    if not downtime:
        raise HTTPException(status_code=404, detail="Downtime record not found")

    if downtime.closed_dt:
        raise HTTPException(status_code=400, detail="Downtime is already closed")

    downtime.inprogress_dt = datetime.now()
    commit()

    return downtime_to_response(downtime)


# PUT endpoint for supervisor to close the downtime (set closed_dt)
@router.put("/supervisor/downtimes/{downtime_id}/close", response_model=DowntimeResponse)
@db_session
def close_downtime(downtime_id: int, action: DowntimeAction):
    """
    Close a downtime by setting the closed_dt to the current timestamp and recording action taken
    """
    downtime = MachineDowntimes.get(id=downtime_id)

    if not downtime:
        raise HTTPException(status_code=404, detail="Downtime record not found")

    if downtime.closed_dt:
        raise HTTPException(status_code=400, detail="Downtime is already closed")

    # Option to automatically set inprogress_dt if not already set
    if not downtime.inprogress_dt:
        downtime.inprogress_dt = datetime.now()

    downtime.closed_dt = datetime.now()
    downtime.action_taken = action.action_taken

    commit()

    return downtime_to_response(downtime)


# GET endpoint to view a specific downtime
@router.get("/downtimes", response_model=List[DowntimeResponse])
@db_session
def get_all_downtimes():
    """
    Get all downtime records
    """
    downtimes = select(d for d in MachineDowntimes)[:]  # Fetch all records
    return [downtime_to_response(d) for d in downtimes]



##################################################################
# @router.get("/metrics22/machine-performance")
# @db_session
# def get_machine_performance_metrics(machine_id: Optional[int] = None):
#     """
#     Calculate MTTR and MTBF for machines with proper handling of all scenarios.
#     """
#     # Get all downtimes
#     query = select(d for d in MachineDowntimes)
#
#     if machine_id is not None:
#         query = query.filter(lambda d: d.machine_id == machine_id)
#
#     all_downtimes = list(query.order_by(MachineDowntimes.machine_id, MachineDowntimes.open_dt))
#
#     if not all_downtimes:
#         raise HTTPException(status_code=404, detail="No downtimes found")
#
#     machine_downtimes = defaultdict(list)
#     for downtime in all_downtimes:
#         machine_downtimes[downtime.machine_id].append(downtime)
#
#     current_time = datetime.now()
#     result = {}
#
#     for machine_id, machine_records in machine_downtimes.items():
#         # Sort records by open_dt
#         machine_records.sort(key=lambda x: x.open_dt)
#
#         # Calculate MTTR from closed downtimes
#         closed_downtimes = [d for d in machine_records if d.closed_dt is not None]
#         repair_times = [(d.closed_dt - d.open_dt).total_seconds() / 3600 for d in closed_downtimes]
#         mttr = sum(repair_times) / len(repair_times) if repair_times else 0
#
#         # Calculate MTBF between any closed downtime and the next opened downtime
#         between_failure_times = []
#
#         # Mix closed and all downtimes for MTBF calculation
#         for i in range(len(closed_downtimes)):
#             current_closed_dt = closed_downtimes[i].closed_dt
#
#             # Find the next downtime (if any) after this closed one
#             next_downtime_index = -1
#             for j, downtime in enumerate(machine_records):
#                 if downtime.open_dt > current_closed_dt:
#                     next_downtime_index = j
#                     break
#
#             # If there is a next downtime, calculate time between
#             if next_downtime_index != -1:
#                 next_open_dt = machine_records[next_downtime_index].open_dt
#                 between_time = (next_open_dt - current_closed_dt).total_seconds() / 3600
#                 between_failure_times.append(between_time)
#
#         # Only add current uptime if the last downtime record is closed
#         if machine_records and machine_records[-1].closed_dt is not None:
#             last_closed_dt = machine_records[-1].closed_dt
#             current_uptime = (current_time - last_closed_dt).total_seconds() / 3600
#             between_failure_times.append(current_uptime)
#
#         mtbf = sum(between_failure_times) / len(between_failure_times) if between_failure_times else 0
#
#         result[machine_id] = {
#             "mttr": round(mttr, 2),
#             "mtbf": round(mtbf, 2),
#             "total_failures": len(machine_records),
#             "unit": "hours"
#         }
#
#     return {
#         "machines": result,
#         "timestamp": current_time.isoformat()
#     }
#
#
# @router.get("/metrics22/machine-performance/{machine_id}")
# @db_session
# def get_single_machine_performance(machine_id: int):
#     """
#     Get MTTR and MTBF metrics for a specific machine
#     """
#     return get_machine_performance_metrics(machine_id=machine_id)