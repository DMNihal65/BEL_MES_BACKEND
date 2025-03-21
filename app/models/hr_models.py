from pony.orm import Required, Set, PrimaryKey
from ..database.connection import db

class Employee(db.Entity):
    _table_ = ('hr_schema', 'employees')  # Specify schema and table name
    
    id = PrimaryKey(int, auto=True)
    name = Required(str)
    email = Required(str, unique=True)
    department = Required(str)
    salary_records = Set('SalaryRecord')  # Relationship with finance schema 