from pony.orm import Required, Optional, PrimaryKey
from datetime import date
from ..database.connection import db

class SalaryRecord(db.Entity):
    _table_ = ('finance_schema', 'salary_records')  # Specify schema and table name
    
    id = PrimaryKey(int, auto=True)
    employee = Required('Employee')
    amount = Required(float)
    payment_date = Required(date)
    bonus = Optional(float) 