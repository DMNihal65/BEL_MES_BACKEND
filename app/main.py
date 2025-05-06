from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .database.connection import connect_to_db
from .routes import hr_routes, finance_routes, master_order_routes, pokayoke
from .api.v1.endpoints import document_management, inventoryv1, priority_scheduling, pdc, operator_log
from .api.v1.endpoints import component_status, programs, daily_production, operator_login, toolsprograms
from .routes import hr_routes, finance_routes, master_order_routes
from .api.v1.endpoints import document_management, inventoryv1, priority_scheduling,mttr_mtbf
from .api.v1.endpoints import component_status, programs, daily_production, operator_login, toolsprograms,notification_service
from .api.v1.endpoints import auth, planning, mpp, operations, scheduled, dynamic_rescheduling, comp_maintainance, comp_operator, document_management_v2, production_monitoring, production_logs,quality, document_management
from .api.v1.endpoints import notification_service


app = FastAPI(title="BEL MES API")


# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
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
app.include_router(comp_maintainance.router)
app.include_router(comp_operator.router)
app.include_router(component_status.router)
app.include_router(scheduled.router)
app.include_router(pdc.router)
app.include_router(operator_log.router)
app.include_router(production_logs.router)
app.include_router(priority_scheduling.router)
app.include_router(dynamic_rescheduling.router)
app.include_router(programs.router)
app.include_router(quality.router)
app.include_router(notification_service.router)
app.include_router(pokayoke.router)

app.include_router(daily_production.router)
app.include_router(inventoryv1.router, prefix="/api/v1")
app.include_router(mttr_mtbf.router)

#######
app.include_router(document_management_v2.router, prefix="/api/v1/document-management", tags=["documents"])
app.include_router(inventoryv1.router, prefix="/api/v1")
app.include_router(production_monitoring.router, tags=["production_monitoring"])
app.include_router(toolsprograms.router)


@app.get("/")
def read_root():
    return {"message": "BEL MES API"}


# uvicorn app.main:app --reload
# uvicorn app.main:app --host 172.18.7.88 --port 6611 --reload
# uvicorn app.main:app --host 172.18.7.88 --port 7676 --reload
# uvicorn app.main:app --host 172.18.7.89 --port 7000 --reload
# uvicorn app.main:app --host 172.18.7.88 --port 4537 --reload

# uvicorn app.main:app --host 172.18.7.88 --port 9988 --reload


# uvicorn app.main:app --host 172.18.7.88 --port 6767 --reload

# uvicorn app.main:app --host 172.18.7.89 --port 4470 --reload