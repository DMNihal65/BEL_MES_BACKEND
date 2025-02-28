from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pony.orm import db_session, select
from datetime import datetime
from typing import List
from pydantic import BaseModel
from decimal import Decimal

from backend.BEL_MES_BACKEND.app.models import oee
from ....database.connection import connect_to_db
from ....models.production import ShiftSummary

app = FastAPI(
    title="Production API",
    description="API for accessing production data",
    version="1.0.0"
)

# CORS middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Connect to database on startup
@app.on_event("startup")
async def startup_event():
    connect_to_db()

# Pydantic model for response
class ShiftSummaryResponse(BaseModel):
    id: int
    machine_id: int
    shift: int
    timestamp: datetime
    off_time: str | None
    idle_time: str | None
    production_time: str | None
    total_parts: int | None
    good_parts: int | None
    bad_parts: int | None
    availability: float | None
    performance: float | None
    quality: float | None
    availability_loss: float | None
    performance_loss: float | None
    quality_loss: float | None
    oee: float | None

    class Config:
        from_attributes = True


@app.get("/api/shift-summary/", response_model=List[ShiftSummaryResponse])
@db_session
def get_shift_summary(machine_id: int, shift_time: datetime):
    try:
        # Query exact match for the timestamp and machine_id
        query_results = select(s for s in ShiftSummary 
                             if s.machine_id == machine_id 
                             and s.timestamp.date() == shift_time.date())
        
        results = list(query_results)
        
        if not results:
            raise HTTPException(
                status_code=404, 
                detail=f"No data found for machine_id {machine_id} at {shift_time}"
            )

        # Convert the results to the response model format
        formatted_results = []
        for data in results:
            formatted_results.append(
                ShiftSummaryResponse(
                    id=data.id,
                    machine_id=data.machine_id,
                    shift=data.shift,
                    timestamp=data.timestamp,
                    off_time=str(data.off_time) if data.off_time else None,
                    idle_time=str(data.idle_time) if data.idle_time else None,
                    production_time=str(data.production_time) if data.production_time else None,
                    total_parts=data.total_parts,
                    good_parts=data.good_parts,
                    bad_parts=data.bad_parts,
                    availability=float(data.availability) if data.availability else None,
                    performance=float(data.performance) if data.performance else None,
                    quality=float(data.quality) if data.quality else None,
                    availability_loss=float(data.availability_loss) if data.availability_loss else None,
                    performance_loss=float(data.performance_loss) if data.performance_loss else None,
                    quality_loss=float(data.quality_loss) if data.quality_loss else None,
                    oee=float(data.oee) if data.oee else None
                )
            )

        return formatted_results

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch shift summary: {str(e)}"
        )


class ShiftAverageResponse(BaseModel):
    shift_time: datetime
    total_machines: int
    avg_availability: float | None
    avg_performance: float | None
    avg_quality: float | None
    avg_availability_loss: float | None
    avg_performance_loss: float | None
    avg_quality_loss: float | None
    avg_oee: float | None

    class Config:
        from_attributes = True

# Add this new endpoint to your existing FastAPI app
@app.get("/api/shopfloor-oee/", response_model=ShiftAverageResponse)
@db_session
def get_shift_averages(shift_time: datetime):
    try:
        # Query to get records for the specified date
        query_results = select(s for s in ShiftSummary 
                             if s.timestamp.date() == shift_time.date())
        
        results = list(query_results)
        
        if not results:
            raise HTTPException(
                status_code=404, 
                detail=f"No data found for shift time {shift_time}"
            )

        # Calculate total unique machines
        unique_machines = len(set(r.machine_id for r in results))

        # Calculate averages
        avg_metrics = {
            'availability': sum(float(r.availability or 0) for r in results) / len(results),
            'performance': sum(float(r.performance or 0) for r in results) / len(results),
            'quality': sum(float(r.quality or 0) for r in results) / len(results),
            'availability_loss': sum(float(r.availability_loss or 0) for r in results) / len(results),
            'performance_loss': sum(float(r.performance_loss or 0) for r in results) / len(results),
            'quality_loss': sum(float(r.quality_loss or 0) for r in results) / len(results),
            'oee': sum(float(r.oee or 0) for r in results) / len(results)
        }

        # Round all values to 2 decimal places
        avg_metrics = {k: round(v, 2) for k, v in avg_metrics.items()}

        return ShiftAverageResponse(
            shift_time=shift_time,
            total_machines=unique_machines,
            avg_availability=avg_metrics['availability'],
            avg_performance=avg_metrics['performance'],
            avg_quality=avg_metrics['quality'],
            avg_availability_loss=avg_metrics['availability_loss'],
            avg_performance_loss=avg_metrics['performance_loss'],
            avg_quality_loss=avg_metrics['quality_loss'],
            avg_oee=avg_metrics['oee']
        )

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to calculate shift averages: {str(e)}"
        )



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(oee, host="0.0.0.0", port=8001)