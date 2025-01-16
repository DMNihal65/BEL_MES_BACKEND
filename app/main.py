from fastapi import FastAPI
from .database.connection import connect_to_db
from .routes import hr_routes, finance_routes, master_order_routes
from .api.v1.endpoints import auth, planning

app = FastAPI(title="Company Management API")

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

@app.get("/")
def read_root():
    return {"message": "Welcome to Company Management API"} 


# uvicorn app.main:app --reload