from fastapi import APIRouter, HTTPException, Query
from typing import List
from datetime import date
from pony.orm import db_session, select, desc, distinct

from app.models.oee import ShiftAvailability
from app.schemas.oee import MachineIDResponse

router = APIRouter(prefix="/oee", tags=["oee"])

@router.get("/machines", response_model=List[MachineIDResponse])
@db_session
def get_machine_ids():
    try:
        unique_machines = select(distinct(s.machine_id) for s in ShiftAvailability)
        return [{"machine_id": machine_id} for machine_id in unique_machines]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
