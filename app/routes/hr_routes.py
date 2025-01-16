from fastapi import APIRouter, HTTPException
from pony.orm import db_session
from typing import List
from ..models.hr_models import Employee
from ..schemas.hr_schemas import EmployeeCreate, EmployeeResponse

router = APIRouter(prefix="/hr", tags=["HR"])

@router.post("/employees/", response_model=EmployeeResponse)
@db_session
def create_employee(employee: EmployeeCreate):
    db_employee = Employee(
        name=employee.name,
        email=employee.email,
        department=employee.department
    )
    return db_employee

@router.get("/employees/", response_model=List[EmployeeResponse])
@db_session
def get_employees():
    return list(Employee.select())

@router.get("/employees/{employee_id}", response_model=EmployeeResponse)
@db_session
def get_employee(employee_id: int):
    employee = Employee.get(id=employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee

@router.put("/employees/{employee_id}", response_model=EmployeeResponse)
@db_session
def update_employee(employee_id: int, employee: EmployeeCreate):
    db_employee = Employee.get(id=employee_id)
    if not db_employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    db_employee.name = employee.name
    db_employee.email = employee.email
    db_employee.department = employee.department
    return db_employee

@router.delete("/employees/{employee_id}")
@db_session
def delete_employee(employee_id: int):
    employee = Employee.get(id=employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    employee.delete()
    return {"message": "Employee deleted successfully"} 