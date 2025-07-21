from decimal import Decimal

from pony.orm import Required, Set, PrimaryKey, Optional, composite_key, select
from datetime import datetime, time
from ..database.connection import db
from .master_order import Operation, Order, Program  # Add Program import
from .scheduled import PlannedScheduleItem
import logging
from typing import Dict, Any, Optional as TypingOptional

logger = logging.getLogger(__name__)


class StatusLookup(db.Entity):
    """Entity class for status lookup table in production schema"""
    _table_ = ('production', 'status_lookup')

    status_id = PrimaryKey(int)
    status_name = Required(str, unique=True)
    machine_statuses = Set('MachineRaw')
    machine_statuses_live = Set('MachineRawLive')


class MachineRaw(db.Entity):
    """Entity class for machine_raw table in livedata schema"""
    _table_ = ('production', 'machine_raw')

    id = PrimaryKey(int, auto=True)
    machine_id = Required(int)
    timestamp = Required(datetime, default=lambda: datetime.utcnow())
    status = Required(StatusLookup)
    op_mode = Optional(int)
    prog_status = Optional(int)
    selected_program = Optional(str)
    active_program = Optional(str)
    program_number = Optional(str)
    part_count = Optional(int)
    job_in_progress = Optional(int)
    part_status = Optional(int)

    scheduled_job = Optional(Operation, reverse='machine_raw_1')
    actual_job = Optional(Operation, reverse='machine_raw_2')


class MachineRawLive(db.Entity):
    """Entity class for machine_raw table in livedata schema"""
    _table_ = ('production', 'machine_raw_live')

    machine_id = PrimaryKey(int)
    timestamp = Required(datetime, default=lambda: datetime.utcnow())
    status = Required(StatusLookup)
    op_mode = Optional(int)
    prog_status = Optional(int)
    selected_program = Optional(str)
    active_program = Optional(str)
    part_count = Optional(int)
    job_status = Optional(int)
    job_in_progress = Optional(int)
    program_number = Optional(int)
    scheduled_job = Optional(Operation, reverse='machine_raw_live_1')
    actual_job = Optional(Operation, reverse='machine_raw_live_2')

    def get_order_details(self) -> TypingOptional[Dict[str, Any]]:
        """
        Get associated order details through actual_job, scheduled_job, or job_in_progress relationships
        Improved version with better error handling and performance
        """
        try:
            # Strategy 1: Try actual_job first (highest priority)
            order_details = self._get_order_from_actual_job()
            if order_details:
                logger.debug(f"Machine {self.machine_id}: Found order details via actual_job")
                return order_details

            # Strategy 2: Try scheduled_job (medium priority)  
            order_details = self._get_order_from_scheduled_job()
            if order_details:
                logger.debug(f"Machine {self.machine_id}: Found order details via scheduled_job")
                return order_details

            # Strategy 3: Try job_in_progress (lowest priority)
            order_details = self._get_order_from_job_in_progress()
            if order_details:
                logger.debug(f"Machine {self.machine_id}: Found order details via job_in_progress")
                return order_details

            # No order details found through any method
            logger.debug(f"Machine {self.machine_id}: No order details found through any available method")
            return None

        except Exception as e:
            logger.error(f"Machine {self.machine_id}: Unexpected error in get_order_details: {str(e)}")
            return None

    def _get_order_from_actual_job(self) -> TypingOptional[Dict[str, Any]]:
        """Get order details from actual_job relationship"""
        try:
            if not self.actual_job:
                return None

            operation = self.actual_job
            if not operation:
                logger.debug(f"Machine {self.machine_id}: actual_job reference exists but operation is None")
                return None

            return self._extract_order_details_from_operation(operation, "actual_job")

        except Exception as e:
            logger.error(f"Machine {self.machine_id}: Error processing actual_job: {str(e)}")
            return None

    def _get_order_from_scheduled_job(self) -> TypingOptional[Dict[str, Any]]:
        """Get order details from scheduled_job relationship"""
        try:
            if not self.scheduled_job:
                return None

            operation = self.scheduled_job
            if not operation:
                logger.debug(f"Machine {self.machine_id}: scheduled_job reference exists but operation is None")
                return None

            return self._extract_order_details_from_operation(operation, "scheduled_job")

        except Exception as e:
            logger.error(f"Machine {self.machine_id}: Error processing scheduled_job: {str(e)}")
            return None

    def _get_order_from_job_in_progress(self) -> TypingOptional[Dict[str, Any]]:
        """Get order details from job_in_progress relationship via PlannedScheduleItem"""
        try:
            if not self.job_in_progress:
                return None

            # Import here to avoid circular imports
            from .scheduled import PlannedScheduleItem

            # Get the schedule item by ID with error handling
            try:
                schedule_item = PlannedScheduleItem.get(id=self.job_in_progress)
            except Exception as query_error:
                logger.error(f"Machine {self.machine_id}: Error querying PlannedScheduleItem with ID {self.job_in_progress}: {str(query_error)}")
                return None

            if not schedule_item:
                logger.debug(f"Machine {self.machine_id}: No PlannedScheduleItem found with ID {self.job_in_progress}")
                return None

            logger.debug(f"Machine {self.machine_id}: Found schedule item with ID {schedule_item.id}")

            # Try to get operation from schedule item
            operation = getattr(schedule_item, 'operation', None)
            order = getattr(schedule_item, 'order', None)

            # If we have both operation and order, prefer that
            if operation and order:
                logger.debug(f"Machine {self.machine_id}: Found both operation and order from schedule item")
                return {
                    'production_order': getattr(order, 'production_order', None),
                    'part_number': getattr(order, 'part_number', None),
                    'part_description': getattr(order, 'part_description', None),
                    'required_quantity': getattr(order, 'required_quantity', None),
                    'launched_quantity': getattr(order, 'launched_quantity', None),
                    'operation_number': getattr(operation, 'operation_number', None),
                    'operation_description': getattr(operation, 'operation_description', None)
                }

            # If we only have order, use that
            elif order:
                logger.debug(f"Machine {self.machine_id}: Found order (no operation) from schedule item")
                return {
                    'production_order': getattr(order, 'production_order', None),
                    'part_number': getattr(order, 'part_number', None),
                    'part_description': getattr(order, 'part_description', None),
                    'required_quantity': getattr(order, 'required_quantity', None),
                    'launched_quantity': getattr(order, 'launched_quantity', None),
                    'operation_number': None,
                    'operation_description': None
                }

            # If we only have operation, try to get order from it
            elif operation:
                logger.debug(f"Machine {self.machine_id}: Found operation (no direct order) from schedule item")
                return self._extract_order_details_from_operation(operation, "job_in_progress")

            else:
                logger.debug(f"Machine {self.machine_id}: Schedule item exists but has no operation or order")
                return None

        except Exception as e:
            logger.error(f"Machine {self.machine_id}: Error processing job_in_progress: {str(e)}")
            return None

    def _extract_order_details_from_operation(self, operation, source: str) -> TypingOptional[Dict[str, Any]]:
        """Extract order details from an operation object"""
        try:
            if not operation:
                return None

            # Safely get operation details
            operation_id = getattr(operation, 'id', None)
            operation_number = getattr(operation, 'operation_number', None)
            operation_description = getattr(operation, 'operation_description', None)

            logger.debug(f"Machine {self.machine_id}: Processing operation {operation_id} from {source}")

            # Get order from operation
            order = getattr(operation, 'order', None)
            if not order:
                logger.debug(f"Machine {self.machine_id}: No order found for operation {operation_id} from {source}")
                return None

            # Safely extract order details
            production_order = getattr(order, 'production_order', None)
            part_number = getattr(order, 'part_number', None)

            logger.debug(f"Machine {self.machine_id}: Found order PO={production_order}, Part={part_number} from {source}")

            return {
                'production_order': production_order,
                'part_number': part_number,
                'part_description': getattr(order, 'part_description', None),
                'required_quantity': getattr(order, 'required_quantity', None),
                'launched_quantity': getattr(order, 'launched_quantity', None),
                'operation_number': operation_number,
                'operation_description': operation_description
            }

        except Exception as e:
            logger.error(f"Machine {self.machine_id}: Error extracting order details from operation: {str(e)}")
            return None

    def get_summary_info(self) -> Dict[str, Any]:
        """Get a summary of machine information for logging/debugging"""
        try:
            return {
                'machine_id': self.machine_id,
                'status': getattr(self.status, 'status_name', 'Unknown') if self.status else 'Unknown',
                'timestamp': self.timestamp.isoformat() if self.timestamp else None,
                'has_actual_job': bool(self.actual_job),
                'has_scheduled_job': bool(self.scheduled_job),
                'has_job_in_progress': bool(self.job_in_progress),
                'job_in_progress_id': self.job_in_progress,
                'selected_program': self.selected_program,
                'active_program': self.active_program,
                'part_count': self.part_count
            }
        except Exception as e:
            logger.error(f"Error getting summary info for machine {self.machine_id}: {str(e)}")
            return {'machine_id': self.machine_id, 'error': str(e)}

    def __repr__(self):
        """String representation for debugging"""
        try:
            status_name = getattr(self.status, 'status_name', 'Unknown') if self.status else 'Unknown'
            return f"<MachineRawLive(machine_id={self.machine_id}, status='{status_name}', timestamp={self.timestamp})>"
        except Exception:
            return f"<MachineRawLive(machine_id={self.machine_id})>"


class ShiftSummary(db.Entity):
    """Shift-wise production summary"""
    _table_ = ('production', 'shift_summary')

    id = PrimaryKey(int, auto=True)
    machine_id = Required(int)
    shift = Required(int)
    timestamp = Required(datetime)

    updatedate = Required(datetime, default=lambda: datetime.utcnow(), auto=True)

    off_time = Optional(time)
    idle_time = Optional(time)
    production_time = Optional(time)

    total_parts = Optional(int)
    good_parts = Optional(int)
    bad_parts = Optional(int)

    availability = Optional(Decimal, precision=5, scale=2)
    performance = Optional(Decimal, precision=5, scale=2)
    quality = Optional(Decimal, precision=5, scale=2)

    availability_loss = Optional(Decimal, precision=5, scale=2)
    performance_loss = Optional(Decimal, precision=5, scale=2)
    quality_loss = Optional(Decimal, precision=5, scale=2)

    oee = Optional(Decimal, precision=5, scale=2)


class ShiftInfo(db.Entity):
    """Shift timing configuration"""
    _table_ = ('production', 'shift_info')

    id = PrimaryKey(int, auto=True)
    start_time = Required(time)
    end_time = Required(time)


class ConfigInfo(db.Entity):
    """Shift timing configuration"""
    _table_ = ('production', 'config_info')

    id = PrimaryKey(int, auto=True)
    machine_id = Required(int, unique=True)
    shift_duration = Required(int)
    planned_non_production_time = Required(int)
    planned_downtime = Required(int)
    updatedate = Required(datetime, default=lambda: datetime.utcnow(), auto=True)


class MachineDowntimes(db.Entity):
    _table_ = ('production', 'machine_downtimes')

    id = PrimaryKey(int, auto=True)
    machine_id = Required(int)
    # status = Required(int)
    priority = Optional(int)
    category = Optional(str, nullable=True)
    description = Optional(str, nullable=True)
    open_dt = Required(datetime)
    inprogress_dt = Optional(datetime)
    closed_dt = Optional(datetime)
    reported_by = Optional(int)
    action_taken = Optional(str, nullable=True)

class OEEIssue(db.Entity):
    _table_ = ('production', 'oee_issue')
    category = Required(str)
    description = Required(str)
    machine = Required(int)  # just an ID, not a foreign key
    timestamp = Required(datetime, default=datetime.utcnow)
    reported_by = Required(int)
