from fastapi import APIRouter, HTTPException
from pony.orm import db_session
from typing import List
from ..models.finance_models import SalaryRecord
from ..models.hr_models import Employee
from ..schemas.finance_schemas import SalaryRecordCreate, SalaryRecordResponse

router = APIRouter(prefix="/finance", tags=["Finance"])

@router.post("/salary-records/", response_model=SalaryRecordResponse)
@db_session
def create_salary_record(salary_record: SalaryRecordCreate):
    employee = Employee.get(id=salary_record.employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    db_salary_record = SalaryRecord(
        employee=employee,
        amount=salary_record.amount,
        payment_date=salary_record.payment_date,
        bonus=salary_record.bonus
    )
    return db_salary_record

@router.get("/salary-records/", response_model=List[SalaryRecordResponse])
@db_session
def get_salary_records():
    return list(SalaryRecord.select())

@router.get("/salary-records/{record_id}", response_model=SalaryRecordResponse)
@db_session
def get_salary_record(record_id: int):
    record = SalaryRecord.get(id=record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Salary record not found")
    return record

@router.delete("/salary-records/{record_id}")
@db_session
def delete_salary_record(record_id: int):
    record = SalaryRecord.get(id=record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Salary record not found")
    record.delete()
    return {"message": "Salary record deleted successfully"} 