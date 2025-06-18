from pony.orm import *
from datetime import datetime, date

from . import Machine
from .inventoryv1 import CalibrationSchedule
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


# PokaYoke Models

class PokaYokeChecklist(db.Entity):
    """Defines a checklist template that can be assigned to machines"""
    _table_ = ("logs", "pokayoke_checklists")
    id = PrimaryKey(int, auto=True)
    name = Required(str)
    description = Optional(str)
    created_at = Required(datetime, default=datetime.now)
    created_by = Required(str)  # User ID who created the checklist
    is_active = Required(bool, default=True)
    items = Set('PokaYokeChecklistItem')
    machine_assignments = Set('PokaYokeChecklistMachineAssignment')
    completed_logs = Set('PokaYokeCompletedLog')

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "is_active": self.is_active,
            "items": [item.to_dict() for item in self.items]
        }


class PokaYokeChecklistItem(db.Entity):
    """Individual checklist items within a checklist template"""
    _table_ = ("logs", "pokayoke_checklist_items")
    id = PrimaryKey(int, auto=True)
    checklist = Required(PokaYokeChecklist)
    item_text = Required(str)  # The instruction or check to perform
    sequence_number = Required(int)  # Order within the checklist
    item_type = Required(str)  # e.g., 'boolean', 'numerical', 'text'
    is_required = Required(bool, default=True)
    expected_value = Optional(str)  # Expected value or range if applicable
    created_at = Required(datetime, default=datetime.now)

    def to_dict(self):
        return {
            "id": self.id,
            "item_text": self.item_text,
            "sequence_number": self.sequence_number,
            "item_type": self.item_type,
            "is_required": self.is_required,
            "expected_value": self.expected_value
        }


class PokaYokeChecklistMachineAssignment(db.Entity):
    """Maps checklists to machines (many-to-many relationship)"""
    _table_ = ("logs", "pokayoke_machine_assignments")
    id = PrimaryKey(int, auto=True)
    checklist = Required(PokaYokeChecklist)
    machine_id = Required(int)  # Reference to Machine table
    machine_make = Optional(str)
    assigned_at = Required(datetime, default=datetime.now)
    assigned_by = Required(str)  # User ID who assigned
    is_active = Required(bool, default=True)

    def to_dict(self):
        return {
            "id": self.id,
            "checklist_id": self.checklist.id,
            "checklist_name": self.checklist.name,
            "machine_id": self.machine_id,
            "machine_make": self.machine_make,
            "assigned_at": self.assigned_at,
            "assigned_by": self.assigned_by,
            "is_active": self.is_active
        }


class PokaYokeCompletedLog(db.Entity):
    """Log of completed checklists by operators"""
    _table_ = ("logs", "pokayoke_completed_logs")
    id = PrimaryKey(int, auto=True)
    checklist = Required(PokaYokeChecklist)
    machine_id = Required(int)
    operator_id = Required(str)  # User ID who completed the checklist
    production_order = Optional(str)  # Production order number
    part_number = Optional(str)
    completed_at = Required(datetime, default=datetime.now)
    all_items_passed = Required(bool)
    comments = Optional(str)
    item_responses = Set('PokaYokeItemResponse')

    def to_dict(self):
        return {
            "id": self.id,
            "checklist_id": self.checklist.id,
            "checklist_name": self.checklist.name,
            "machine_id": self.machine_id,
            "operator_id": self.operator_id,
            "production_order": self.production_order,
            "part_number": self.part_number,
            "completed_at": self.completed_at,
            "all_items_passed": self.all_items_passed,
            "comments": self.comments,
            "responses": [resp.to_dict() for resp in self.item_responses]
        }


class PokaYokeItemResponse(db.Entity):
    """Individual item responses within a completed checklist"""
    _table_ = ("logs", "pokayoke_item_responses")
    id = PrimaryKey(int, auto=True)
    completed_log = Required(PokaYokeCompletedLog)
    item_id = Required(int)  # Reference to ChecklistItem
    item_text = Required(str)  # Denormalized for historical record
    response_value = Required(str)  # The actual value/response from the operator
    is_conforming = Required(bool)  # Whether the response meets requirements
    timestamp = Required(datetime, default=datetime.now)

    def to_dict(self):
        return {
            "id": self.id,
            "item_id": self.item_id,
            "item_text": self.item_text,
            "response_value": self.response_value,
            "is_conforming": self.is_conforming,
            "timestamp": self.timestamp
        }


class MachineCalibrationLog(db.Entity):
    _table_ = ("logs", "machine_calibration_logs")
    id = PrimaryKey(int, auto=True)
    timestamp = Required(datetime, default=datetime.now)
    calibration_due_date = Optional(date)
    machine_id = Optional(Machine)

    def after_insert(self):
        """
        Hook that runs after a new calibration log is inserted.
        Schedule an async task to send a notification.
        """
        print(f"after_insert triggered for MachineCalibrationLog with ID: {self.id}")

        # Import here to avoid circular imports
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        try:
            # Create a function to call the notification sender
            def send_notification():
                from app.api.v1.endpoints.notification_service import send_calibration_notification

                # Get the event loop or create a new one
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                # We need to get the entity within db_session in this thread
                from pony.orm import db_session
                with db_session:
                    # Get the entity by ID to ensure it's properly loaded
                    log_entry = MachineCalibrationLog.get(id=self.id)
                    if log_entry:
                        # Run the async notification in the loop
                        asyncio.run_coroutine_threadsafe(
                            send_calibration_notification(log_entry),
                            loop
                        )
                        print(f"Notification for log ID {self.id} scheduled successfully")
                    else:
                        print(f"Could not find log with ID {self.id} for notification")

            # Run the notification sender in a separate thread
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(send_notification)

            print(f"Notification task submitted for calibration log ID: {self.id}")
        except Exception as e:
            print(f"Error scheduling notification for log ID {self.id}: {str(e)}")
            import traceback
            traceback.print_exc()


class InstrumentCalibrationLog(db.Entity):
    _table_ = ("logs", "instrument_calibration_logs")
    id = PrimaryKey(int, auto=True)
    timestamp = Required(datetime, default=datetime.now)
    calibration_due_date = Optional(date)
    instrument_id = Optional(CalibrationSchedule)

    def after_insert(self):
        """
        Hook that runs after a new instrument calibration log is inserted.
        Schedule an async task to send a notification.
        """
        print(f"after_insert triggered for InstrumentCalibrationLog with ID: {self.id}")

        # Import here to avoid circular imports
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        try:
            # Create a function to call the notification sender
            def send_notification():
                from app.api.v1.endpoints.notification_service import send_instrument_calibration_notification

                # Get the event loop or create a new one
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                # We need to get the entity within db_session in this thread
                from pony.orm import db_session
                with db_session:
                    # Get the entity by ID to ensure it's properly loaded
                    log_entry = InstrumentCalibrationLog.get(id=self.id)
                    if log_entry:
                        # Run the async notification in the loop
                        asyncio.run_coroutine_threadsafe(
                            send_instrument_calibration_notification(log_entry),
                            loop
                        )
                        print(f"Instrument notification for log ID {self.id} scheduled successfully")
                    else:
                        print(f"Could not find instrument log with ID {self.id} for notification")

            # Run the notification sender in a separate thread
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(send_notification)

            print(f"Instrument notification task submitted for calibration log ID: {self.id}")
        except Exception as e:
            print(f"Error scheduling instrument notification for log ID {self.id}: {str(e)}")
            import traceback
            traceback.print_exc()
