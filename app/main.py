from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware  # Add this import


from .database.connection import connect_to_db
from .routes import hr_routes, finance_routes, master_order_routes
from .api.v1.endpoints import auth, planning, mpp, operations, component_status, programs, daily_production
from .api.v1.endpoints import auth, planning, mpp, operations, scheduled

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
# app.include_router(hr_routes.router)
# app.include_router(finance_routes.router)
app.include_router(auth.router)
app.include_router(master_order_routes.router)
app.include_router(planning.router)
app.include_router(mpp.router, tags=["mpp"])
app.include_router(operations.router)
app.include_router(component_status.router, tags=["production"])
app.include_router(scheduled.router)
app.include_router(programs.router)
app.include_router(daily_production.router)


@app.get("/")
def read_root():
    return {"message": "BEL MES API"}

# uvicorn app.main:app --reload

# uvicorn app.main:app --host 172.18.7.88 --port 4478 --reload