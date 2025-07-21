import threading
import time
import psutil
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request
from fastapi.responses import JSONResponse
from .database.connection import connect_to_db
from .routes import hr_routes, finance_routes, master_order_routes, pokayoke
from .api.v1.endpoints import document_management, inventoryv1, priority_scheduling, pdc, operator_log
from .api.v1.endpoints import component_status, programs, daily_production, operator_login, toolsprograms
from .routes import hr_routes, finance_routes, master_order_routes
from .api.v1.endpoints import document_management, inventoryv1, priority_scheduling,mttr_mtbf
from .api.v1.endpoints import component_status, programs, daily_production, operator_login, toolsprograms,notification_service
from .api.v1.endpoints import auth, planning, mpp, operations, scheduled, dynamic_rescheduling, comp_maintainance, comp_operator, document_management_v2, production_monitoring, production_logs,quality, document_management, energymonitoring, newlogs, operatorlog2
from .api.v1.endpoints import notification_service,simple_notifications


app = FastAPI(title="BEL MES API")


# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
    expose_headers=["Content-Disposition", "Content-Type", "Content-Length"]
)

# Connect to database on startup
@app.on_event("startup")
async def startup_event():
    try:
        connect_to_db()
    except Exception as e:
        print(f"Error connecting to database: {str(e)}")
        raise e

# Include routers
app.include_router(auth.router)
app.include_router(operator_login.router)
app.include_router(master_order_routes.router)
app.include_router(planning.router)
app.include_router(mpp.router)
app.include_router(operations.router)
app.include_router(newlogs.router)
app.include_router(comp_maintainance.router)
app.include_router(comp_operator.router)
app.include_router(component_status.router)
app.include_router(scheduled.router)
app.include_router(pdc.router)
app.include_router(operator_log.router)
app.include_router(operatorlog2.router)
app.include_router(production_logs.router)
app.include_router(priority_scheduling.router)
app.include_router(dynamic_rescheduling.router)
app.include_router(programs.router)
app.include_router(quality.router)
app.include_router(notification_service.router)
app.include_router(simple_notifications.router)
app.include_router(pokayoke.router)
app.include_router(daily_production.router)
app.include_router(inventoryv1.router)
app.include_router(mttr_mtbf.router)
app.include_router(energymonitoring.router)


#######
app.include_router(document_management_v2.router, prefix="/api/v1/document-management", tags=["documents"])
# app.include_router(inventoryv1.router, prefix="/api/v1")
app.include_router(production_monitoring.router, tags=["production_monitoring"])
app.include_router(toolsprograms.router)


# Global metrics storage (thread-safe)
metrics_lock = threading.Lock()
endpoint_metrics = {}

@app.middleware("http")
async def performance_monitoring_middleware(request: Request, call_next):
    endpoint = request.url.path
    start_time = time.time()
    start_cpu = psutil.Process().cpu_times().user
    response = await call_next(request)
    end_time = time.time()
    end_cpu = psutil.Process().cpu_times().user
    duration = end_time - start_time
    cpu_time = end_cpu - start_cpu
    with metrics_lock:
        if endpoint not in endpoint_metrics:
            endpoint_metrics[endpoint] = {
                "count": 0,
                "total_duration": 0.0,
                "total_cpu_time": 0.0
            }
        endpoint_metrics[endpoint]["count"] += 1
        endpoint_metrics[endpoint]["total_duration"] += duration
        endpoint_metrics[endpoint]["total_cpu_time"] += cpu_time
    return response

@app.get("/performance")
def get_performance_metrics():
    with metrics_lock:
        stats = {}
        for endpoint, data in endpoint_metrics.items():
            count = data["count"]
            stats[endpoint] = {
                "count": count,
                "total_duration": data["total_duration"],
                "avg_duration": data["total_duration"] / count if count else 0,
                "total_cpu_time": data["total_cpu_time"],
                "avg_cpu_time": data["total_cpu_time"] / count if count else 0
            }
    return JSONResponse(content=stats)


@app.get("/")
def read_root():
    return {"message": "BEL MES API"}


# uvicorn app.main:app --reload
# uvicorn app.main:app --host 172.18.7.88 --port 1919 --reload
# uvicorn app.main:app --host 172.18.7.88 --port 1717 --reload
# uvicorn app.main:app --host 172.18.7.88 --port 4455 --reload
# uvicorn app.main:app --host 172.18.7.88 --port 5467 --reload

# uvicorn app.main:app --host 172.18.7.88 --port 6512 --reload


# uvicorn app.main:app --host 172.18.7.88 --port 2929 --reload
# uvicorn app.main:app --host 172.18.7.88 --port 2277 --reload
# uvicorn app.main:app --host 172.18.7.88 --port 8888 --reload
