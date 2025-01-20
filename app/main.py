from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware  # Add this import
from .database.connection import connect_to_db
from .routes import hr_routes, finance_routes, master_order_routes
from .api.v1.endpoints import auth, planning, mpp, operations, document_management

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

# Include routers with proper prefixes
app.include_router(auth.router)  # This will now use /api/v1/auth prefix
app.include_router(master_order_routes.router, prefix="/api/v1")
app.include_router(planning.router, prefix="/api/v1")
app.include_router(mpp.router, prefix="/api/v1", tags=["mpp"])
app.include_router(operations.router, prefix="/api/v1")
app.include_router(document_management.router, prefix="/api/v1")

@app.get("/")
def read_root():
    return {"message": "BEL MES API"}

# uvicorn app.main:app --reload

# uvicorn app.main:app --host 172.18.7.88 --port 2222 --reload
# uvicorn app.main:app --host 172.18.7.89 --port 2222 --reload