from datetime import datetime
from pony.orm import *
from ..database.connection import db

class Project(db.Entity):
    """Project model with separated priorities for scheduling and rescheduling"""
    _table_ = ("production", "projects")
    id = PrimaryKey(int, auto=True)
    name = Required(str)
    priority = Required(int, default=100)  # Original priority field (should not affect rescheduling)
    schedule_batch_priority = Optional(int)  # Priority field specifically for schedule_batch
    schedule_batch_flag = Required(bool, default=False)  # Flag to indicate priority was changed for schedule_batch
    priority_last_updated = Optional(datetime)
    delivery_date = Optional(datetime)
    orders = Set('Order')
    created_at = Required(datetime, default=datetime.utcnow)
    updated_at = Required(datetime, default=datetime.utcnow)

    def before_update(self):
        self.updated_at = datetime.utcnow()
        # If priority changes, update priority_last_updated
        if self._vals_.get('priority') is not None and self._vals_['priority'] != self.priority:
            self.priority_last_updated = datetime.utcnow()