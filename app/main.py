from fastapi import FastAPI
from .core.config import settings
from .core.database import init_db
from .api.v1.endpoints import auth
from fastapi.middleware.cors import CORSMiddleware
from app.models.master_order import db as master_order_db
from app.api.v1.endpoints import planning

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize database
init_db()

# Master Order Database configuration
master_order_db.bind(
    provider='postgres',  # Note: Use 'postgres' instead of 'postgresql'
    user=settings.POSTGRES_USER,
    password=settings.POSTGRES_PASSWORD,
    host=settings.POSTGRES_SERVER,
    database=settings.POSTGRES_DB
)
master_order_db.generate_mapping(create_tables=True)

# Include routers
app.include_router(
    auth.router,
    prefix=f"{settings.API_V1_STR}/auth",
    tags=["authentication"]
) 

app.include_router(planning.router, prefix="/planning", tags=["planning"]) 

#   uvicorn app.main:app --reload --port 8000