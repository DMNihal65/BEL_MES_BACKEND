from fastapi import APIRouter, HTTPException, Query
from typing import List, Dict, Any, Optional
from pony.orm import db_session, select, commit, desc
from datetime import datetime, date
from pydantic import BaseModel

from app.models.logs import (
    MachineStatusLog,
    RawMaterialStatusLog,
    MachineCalibrationLog,
    InstrumentCalibrationLog,
    PokaYokeCompletedLog
)

router = APIRouter(prefix="/api/v1/simple-notifications", tags=["simple-notifications"])


class MarkReadRequest(BaseModel):
    table_name: str
    record_id: int


class NotificationResponse(BaseModel):
    id: int
    table_name: str
    record_data: Dict[str, Any]
    created_at: str
    read: bool


@router.get("/unread", response_model=List[NotificationResponse])
async def get_unread_notifications():
    """
    Get all unread notifications from all specified tables.
    Returns notifications from: MachineStatusLog, RawMaterialStatusLog,
    MachineCalibrationLog, InstrumentCalibrationLog, PokaYokeCompletedLog
    """
    try:
        notifications = []

        with db_session:
            # Get unread machine status logs
            machine_logs = list(select(n for n in MachineStatusLog if not n.read))
            for log in machine_logs:
                notifications.append(NotificationResponse(
                    id=log.id,
                    table_name="MachineStatusLog",
                    record_data=log.to_dict(),
                    created_at=log.updated_at.isoformat(),
                    read=bool(log.read)
                ))

            # Get unread raw material status logs
            material_logs = list(select(n for n in RawMaterialStatusLog if not n.read))
            for log in material_logs:
                notifications.append(NotificationResponse(
                    id=log.id,
                    table_name="RawMaterialStatusLog",
                    record_data=log.to_dict(),
                    created_at=log.updated_at.isoformat(),
                    read=bool(log.read)
                ))

            # Get unread machine calibration logs
            machine_calib_logs = list(select(n for n in MachineCalibrationLog if not n.read))
            for log in machine_calib_logs:
                notifications.append(NotificationResponse(
                    id=log.id,
                    table_name="MachineCalibrationLog",
                    record_data=log.to_dict(),
                    created_at=log.timestamp.isoformat(),
                    read=bool(log.read)
                ))

            # Get unread instrument calibration logs
            instrument_calib_logs = list(select(n for n in InstrumentCalibrationLog if not n.read))
            for log in instrument_calib_logs:
                notifications.append(NotificationResponse(
                    id=log.id,
                    table_name="InstrumentCalibrationLog",
                    record_data=log.to_dict(),
                    created_at=log.timestamp.isoformat(),
                    read=bool(log.read)
                ))

            # Get unread pokayoke completed logs
            pokayoke_logs = list(select(n for n in PokaYokeCompletedLog if not n.read))
            for log in pokayoke_logs:
                notifications.append(NotificationResponse(
                    id=log.id,
                    table_name="PokaYokeCompletedLog",
                    record_data=log.to_dict(),
                    created_at=log.completed_at.isoformat(),
                    read=bool(log.read)
                ))

        # Sort by created_at descending (newest first)
        notifications.sort(key=lambda x: x.created_at, reverse=True)

        return notifications

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching unread notifications: {str(e)}")


@router.post("/mark-read")
async def mark_notification_as_read(request: MarkReadRequest):
    """
    Mark a specific notification as read.
    """
    try:
        with db_session:
            record = None

            # Find the record based on table name
            if request.table_name == "MachineStatusLog":
                record = MachineStatusLog.get(id=request.record_id)
            elif request.table_name == "RawMaterialStatusLog":
                record = RawMaterialStatusLog.get(id=request.record_id)
            elif request.table_name == "MachineCalibrationLog":
                record = MachineCalibrationLog.get(id=request.record_id)
            elif request.table_name == "InstrumentCalibrationLog":
                record = InstrumentCalibrationLog.get(id=request.record_id)
            elif request.table_name == "PokaYokeCompletedLog":
                record = PokaYokeCompletedLog.get(id=request.record_id)
            else:
                raise HTTPException(status_code=400, detail=f"Invalid table name: {request.table_name}")

            if not record:
                raise HTTPException(status_code=404,
                                    detail=f"Record not found in {request.table_name} with ID {request.record_id}")

            # Mark as read
            record.read = True
            commit()

            return {"status": "success", "message": f"Notification marked as read", "record_id": request.record_id}

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error marking notification as read: {str(e)}")


@router.post("/mark-all-read")
async def mark_all_notifications_as_read():
    """
    Mark all unread notifications as read.
    """
    try:
        with db_session:
            # Mark all unread machine status logs as read
            machine_logs = list(select(n for n in MachineStatusLog if not n.read))
            for log in machine_logs:
                log.read = True

            # Mark all unread raw material status logs as read
            material_logs = list(select(n for n in RawMaterialStatusLog if not n.read))
            for log in material_logs:
                log.read = True

            # Mark all unread machine calibration logs as read
            machine_calib_logs = list(select(n for n in MachineCalibrationLog if not n.read))
            for log in machine_calib_logs:
                log.read = True

            # Mark all unread instrument calibration logs as read
            instrument_calib_logs = list(select(n for n in InstrumentCalibrationLog if not n.read))
            for log in instrument_calib_logs:
                log.read = True

            # Mark all unread pokayoke completed logs as read
            pokayoke_logs = list(select(n for n in PokaYokeCompletedLog if not n.read))
            for log in pokayoke_logs:
                log.read = True

            commit()

            return {"status": "success", "message": "All notifications marked as read"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error marking all notifications as read: {str(e)}")


@router.get("/count")
async def get_unread_notification_count():
    """
    Get count of unread notifications from all specified tables.
    """
    try:
        with db_session:
            count = 0

            # Count unread machine status logs
            count += len(list(select(n for n in MachineStatusLog if not n.read)))

            # Count unread raw material status logs
            count += len(list(select(n for n in RawMaterialStatusLog if not n.read)))

            # Count unread machine calibration logs
            count += len(list(select(n for n in MachineCalibrationLog if not n.read)))

            # Count unread instrument calibration logs
            count += len(list(select(n for n in InstrumentCalibrationLog if not n.read)))

            # Count unread pokayoke completed logs
            count += len(list(select(n for n in PokaYokeCompletedLog if not n.read)))

            return {"unread_count": count}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error counting unread notifications: {str(e)}")


@router.get("/by-table/{table_name}")
async def get_unread_notifications_by_table(table_name: str):
    """
    Get unread notifications from a specific table.
    """
    try:
        notifications = []

        with db_session:
            if table_name == "MachineStatusLog":
                logs = list(select(n for n in MachineStatusLog if not n.read))
                for log in logs:
                    notifications.append({
                        "id": log.id,
                        "table_name": "MachineStatusLog",
                        "record_data": log.to_dict(),
                        "created_at": log.updated_at.isoformat(),
                        "read": bool(log.read)
                    })
            elif table_name == "RawMaterialStatusLog":
                logs = list(select(n for n in RawMaterialStatusLog if not n.read))
                for log in logs:
                    notifications.append({
                        "id": log.id,
                        "table_name": "RawMaterialStatusLog",
                        "record_data": log.to_dict(),
                        "created_at": log.updated_at.isoformat(),
                        "read": bool(log.read)
                    })
            elif table_name == "MachineCalibrationLog":
                logs = list(select(n for n in MachineCalibrationLog if not n.read))
                for log in logs:
                    notifications.append({
                        "id": log.id,
                        "table_name": "MachineCalibrationLog",
                        "record_data": log.to_dict(),
                        "created_at": log.timestamp.isoformat(),
                        "read": bool(log.read)
                    })
            elif table_name == "InstrumentCalibrationLog":
                logs = list(select(n for n in InstrumentCalibrationLog if not n.read))
                for log in logs:
                    notifications.append({
                        "id": log.id,
                        "table_name": "InstrumentCalibrationLog",
                        "record_data": log.to_dict(),
                        "created_at": log.timestamp.isoformat(),
                        "read": bool(log.read)
                    })
            elif table_name == "PokaYokeCompletedLog":
                logs = list(select(n for n in PokaYokeCompletedLog if not n.read))
                for log in logs:
                    notifications.append({
                        "id": log.id,
                        "table_name": "PokaYokeCompletedLog",
                        "record_data": log.to_dict(),
                        "created_at": log.completed_at.isoformat(),
                        "read": bool(log.read)
                    })
            else:
                raise HTTPException(status_code=400, detail=f"Invalid table name: {table_name}")

        # Sort by created_at descending (newest first)
        notifications.sort(key=lambda x: x["created_at"], reverse=True)

        return {
            "table_name": table_name,
            "total_notifications": len(notifications),
            "notifications": notifications
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching notifications from {table_name}: {str(e)}")

