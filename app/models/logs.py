from pony.orm import *
from datetime import datetime
from ..database.connection import db  # Import the shared db instance

# Define the database entities in the logs schema
class MachineStatusLog(db.Entity):
    _table_ = ("logs", "machine_status_logs")  # Set schema and table name
    id = PrimaryKey(int, auto=True)
    machine_id = Required(int)
    machine_make = Required(str)
    status_name = Required(str)
    description = Optional(str)
    updated_at = Required(datetime)
    created_by = Optional(str)  # Operator who created the notification
    is_acknowledged = Required(bool, default=False)  # Whether notification is acknowledged
    acknowledged_by = Optional(str)  # User ID who acknowledged
    acknowledged_at = Optional(datetime)  # When it was acknowledged

    def to_dict(self):
        return {
            "id": self.id,
            "machine_id": self.machine_id,
            "machine_make": self.machine_make,
            "status_name": self.status_name,
            "description": self.description,
            "updated_at": self.updated_at,
            "created_by": self.created_by,
            "is_acknowledged": self.is_acknowledged,
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at
        }




class RawMaterialStatusLog(db.Entity):
    _table_ = ("logs", "raw_material_status_logs")  # Set schema and table name
    id = PrimaryKey(int, auto=True)
    material_id = Required(int)
    part_number = Optional(str)
    status_name = Required(str)
    description = Optional(str)
    updated_at = Required(datetime)
    created_by = Optional(str)  # Operator who created the notification
    is_acknowledged = Required(bool, default=False)  # Whether notification is acknowledged
    acknowledged_by = Optional(str)  # User ID who acknowledged
    acknowledged_at = Optional(datetime)  # When it was acknowledged

    def to_dict(self):
        return {
            "id": self.id,
            "material_id": self.material_id,
            "part_number": self.part_number,
            "status_name": self.status_name,
            "description": self.description,
            "updated_at": self.updated_at,
            "created_by": self.created_by,
            "is_acknowledged": self.is_acknowledged,
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at
        }

