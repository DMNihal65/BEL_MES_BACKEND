from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .database.connection import connect_to_db
from .routes import hr_routes, finance_routes, master_order_routes
from .api.v1.endpoints import document_management, inventoryv1, priority_scheduling
from .api.v1.endpoints import component_status, programs, daily_production, operator_login
from .api.v1.endpoints import auth, planning, mpp, operations, scheduled, dynamic_rescheduling, comp_maintainance, comp_operator, document_management_v2, production_monitoring, production_logs, quality

# from .api.v1.endpoints import document_management  # Comment out v1 endpoint
from .api.v1.endpoints import inventoryv1
from .api.v1.endpoints import component_status, programs, daily_production
from .api.v1.endpoints import auth, planning, mpp, operations, scheduled, document_management_v2, production_monitoring
import logging
import os
from datetime import datetime

# Create logs directory if it doesn't exist
log_dir = 'logs'
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Print to console
        logging.FileHandler(os.path.join(log_dir, f'api_{datetime.now().strftime("%Y%m%d")}.log'))  # Save to file
    ]
)

logger = logging.getLogger(__name__)

app = FastAPI(title="BEL MES API")


# Add CORS middleware
# Configure CORS
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
        logger.info("Starting BEL MES API application...")
        logger.info("Attempting database connection...")

        start_time = datetime.now()
        connect_to_db()
        connection_time = datetime.now() - start_time

        logger.info(f"Database connection successful! Connection time: {connection_time.total_seconds():.2f} seconds")
        logger.info("Database connection details:")
        logger.info("----------------------------")
        logger.info(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Host: {app.state.db_host if hasattr(app.state, 'db_host') else 'Not available'}")
        logger.info(f"Database: {app.state.db_name if hasattr(app.state, 'db_name') else 'Not available'}")
        logger.info(f"User: {app.state.db_user if hasattr(app.state, 'db_user') else 'Not available'}")
        logger.info("----------------------------")

    except Exception as e:
        logger.error(f"Database connection failed!")
        logger.error(f"Error details: {str(e)}")
        logger.error("----------------------------")
        raise e

# Include routers
# app.include_router(hr_routes.router)
# app.include_router(finance_routes.router)
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
app.include_router(production_logs.router)
app.include_router(priority_scheduling.router)
app.include_router(dynamic_rescheduling.router)
app.include_router(programs.router)
app.include_router(quality.router)

app.include_router(daily_production.router)

# Comment out the v1 document management router
# app.include_router(document_management.router, prefix="/api/v1")

# Use only v2 document management router
app.include_router(document_management_v2.router, prefix="/api/v1/document-management", tags=["documents"])
app.include_router(inventoryv1.router, prefix="/api/v1")


#######
app.include_router(document_management_v2.router, prefix="/api/v1/document-management", tags=["documents"])
app.include_router(inventoryv1.router, prefix="/api/v1")
app.include_router(production_monitoring.router, tags=["production_monitoring"])

# router = APIRouter(prefix="/api/inventory", tags=["inventory"])
app.include_router(production_monitoring.router, tags=["production_monitoring"])

@app.get("/")
def read_root():
    logger.info("Root endpoint accessed")
    return {"message": "BEL MES API"}

# Add shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down BEL MES API application...")
    logger.info(f"Shutdown timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("----------------------------")

# uvicorn app.main:app --reload
# uvicorn app.main:app --host 172.18.7.88 --port 6970 --reload
# uvicorn app.main:app --host 172.18.7.88 --port 6202 --reload
# uvicorn app.main:app --host 172.18.7.85 --port 7737 --reload

# uvicorn app.main:app --host 172.18.7.88 --port 4470 --reload

# uvicorn app.main:app --host 172.18.7.89 --port 4470 --reload
# uvicorn app.main:app --host 172.18.7.93 --port 9999 --reload