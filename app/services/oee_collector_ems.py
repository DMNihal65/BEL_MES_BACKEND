from datetime import datetime, timedelta
import time
from pony.orm import db_session, commit, select, desc

from app.database.connection import connect_to_db
from app.models.ems import EMSMachine, Recent, State
from app.models.production import MachineRaw, MachineRawLive, StatusLookup, ShiftSummary, ShiftInfo, ConfigInfo

from datetime import time as timett

# Constants for status mapping
STATUS_CODES = {
    "OFF": 0,
    "ON": 1,  # ON state
    "PRODUCTION": 2
}

IGNORED_MACHINE_IDS = [1, 7]


def get_status_lookup_id(status_code):
    """Convert status code to status_lookup ID"""
    with db_session:
        status = StatusLookup.get(status_id=status_code+1)
        if not status:
            raise ValueError(f"Status code {status_code} not found in StatusLookup table")
        return status


@db_session
def get_all_machines():
    """Get all machines from the Machine table"""
    return select(m for m in EMSMachine)[:]


@db_session
def get_machine_state_thresholds(machine_id):
    """
    Get current thresholds for a machine from the check_state table
    Returns a dictionary of states with their current ranges
    """
    thresholds = {}
    machine = EMSMachine.get(id=machine_id)

    if not machine:
        print(f"Machine with ID {machine_id} not found")
        return None

    state_configs = select(s for s in State if s.machine_id == machine)[:]

    for config in state_configs:
        thresholds[config.state] = {
            'start': config.start_current_range,
            'end': config.end_current_range
        }

    return thresholds


@db_session
def get_machine_recent_current(machine_id):
    """Get the most recent current reading for a machine"""
    machine = EMSMachine.get(id=machine_id)

    if not machine:
        print(f"Machine with ID {machine_id} not found")
        return None

    recent = Recent.get(machine_id=machine)

    if not recent:
        print(f"No recent data for machine ID {machine_id}")
        return None

    return {
        'current': recent.current,
        'timestamp': recent.timestamp
    }


@db_session
def get_machine_last_status(machine_id):
    """Get the most recent status from machine_raw_live table"""
    live_status = MachineRawLive.get(machine_id=machine_id)

    if not live_status:
        return None

    return {
        'status_id': live_status.status.status_id,
        'timestamp': live_status.timestamp
    }


@db_session
def determine_machine_state(machine_id):
    """
    Determine the machine state based on current reading and thresholds
    Returns tuple (state_code, current_reading, timestamp)
    """
    # Get current reading
    current_data = get_machine_recent_current(machine_id)
    if not current_data:
        return None

    current_reading = current_data['current']
    timestamp = current_data['timestamp']

    # Get thresholds
    thresholds = get_machine_state_thresholds(machine_id)
    if not thresholds:
        print(f"No thresholds defined for machine ID {machine_id}")
        return None

    # Determine state based on current reading
    for state, range_values in thresholds.items():
        if range_values['start'] <= current_reading <= range_values['end']:
            state_code = STATUS_CODES.get(state.upper())
            if state_code is not None:
                return (state_code, current_reading, timestamp)

    # Default to OFF if no matching threshold found
    print(f"Warning: Current reading {current_reading} for machine ID {machine_id} doesn't match any threshold")
    return (STATUS_CODES["OFF"], current_reading, timestamp)


@db_session
def update_machine_status(machine_id, status_code, timestamp):
    """
    Update the machine status in both MachineRawLive and MachineRaw tables
    Only inserts into MachineRaw if status has changed
    """
    # Get status from StatusLookup
    status = get_status_lookup_id(status_code)

    # Get current status from MachineRawLive
    current_status = get_machine_last_status(machine_id)
    status_changed = False

    # Check if status has changed
    if not current_status or current_status['status_id'] != status_code:
        status_changed = True

    # Update or create MachineRawLive record
    live_record = MachineRawLive.get(machine_id=machine_id)
    if live_record:
        live_record.timestamp = timestamp
        live_record.status = status
    else:
        MachineRawLive(
            machine_id=machine_id,
            timestamp=timestamp,
            status=status,
            op_mode=-9  # Using status_code as op_mode for simplicity
        )

    # Insert into MachineRaw if status changed
    if status_changed:
        MachineRaw(
            machine_id=machine_id,
            timestamp=timestamp,
            status=status,
            op_mode=-9  # Using status_code as op_mode for simplicity
        )
        print(f"Status change recorded for machine ID {machine_id}: New status = {status_code}")

    commit()
    return status_changed


def get_current_shift(timestamp):
    """
    Determine current shift based on timestamp
    Returns (shift_id, shift_start_time, shift_end_time)
    """
    current_time = timestamp.time()

    with db_session:
        shifts = select(s for s in ShiftInfo)[:]

        for shift in shifts:
            start = shift.start_time
            end = shift.end_time

            # Handle shifts that cross midnight
            if start > end:
                if current_time >= start or current_time < end:
                    return shift.id, start, end
            else:
                if start <= current_time < end:
                    return shift.id, start, end

    # If no shift found, use the first shift (assuming 24/7 operation)
    first_shift = shifts[0]
    return first_shift.id, first_shift.start_time, first_shift.end_time

@db_session
def manage_shift_summary(timestamp, machine_id=1, part_count=0, part_status=0):
    """
    Create or update shift summary based on current machine status
    Returns the current shift summary entry
    """
    shift_id, shift_start_time, shift_end_time = get_current_shift(timestamp)

    # Convert shift times to current date
    current_date = timestamp.date()
    shift_start = datetime.combine(current_date, shift_start_time)
    shift_end = datetime.combine(current_date, shift_end_time)

    config_info = ConfigInfo.get(machine_id=machine_id)

    # Handle shifts crossing midnight
    if shift_start_time > shift_end_time:
        if timestamp.time() >= shift_start_time:
            shift_end += timedelta(days=1)
        else:
            shift_start -= timedelta(days=1)

    # Get or create shift summary
    shift_summary = ShiftSummary.get(
        machine_id=machine_id,
        shift=shift_id,
        timestamp=shift_start
    )

    if not shift_summary:
        # Initialize new shift summary with 00:00:00 time
        zero_time = timett(0, 0, 0)
        shift_summary = ShiftSummary(
            machine_id=machine_id,
            shift=shift_id,
            timestamp=shift_start,
            off_time=zero_time,
            idle_time=zero_time,
            production_time=zero_time,
            total_parts=0,
            good_parts=0,
            bad_parts=0,
            availability=0,
            performance=0,
            quality=0,
            availability_loss=0,
            performance_loss=0,
            quality_loss=0,
            oee=0
        )

    # Get all status changes within the current shift using Pony ORM syntax
    status_changes = ([select(s for s in MachineRaw
                              if s.machine_id == machine_id and
                              s.timestamp <= shift_start).order_by(lambda s: desc(s.timestamp)).first()]
                      +
                      select(s for s in MachineRaw
                             if s.machine_id == machine_id and
                             s.timestamp >= shift_start and
                             s.timestamp <= timestamp)[:])

    # Initialize duration counters
    off_duration = timedelta()
    idle_duration = timedelta()
    production_duration = timedelta()

    # Sort status changes by timestamp
    status_changes = [s for s in status_changes if s is not None]
    status_changes = sorted(status_changes, key=lambda x: x.timestamp)

    if len(status_changes) == 1:
        duration = timestamp - shift_start
        if status_changes[0].status.status_id == 0:
            off_duration += duration
        elif status_changes[0].status.status_id == 1:
            idle_duration += duration
        elif status_changes[0].status.status_id == 2:
            production_duration += duration
    else:
        for i in range(1, len(status_changes)):
            if i == 1:
                start_time = max(shift_start, status_changes[i - 1].timestamp)
            else:
                start_time = status_changes[i - 1].timestamp

            end_time = status_changes[i].timestamp

            duration = end_time - start_time
            # print(status_changes[i-1].status.status_id)
            if status_changes[i - 1].status.status_id == 0:
                off_duration += duration
            elif status_changes[i - 1].status.status_id == 1:
                idle_duration += duration
            elif status_changes[i - 1].status.status_id == 2:
                production_duration += duration

    if len(status_changes) > 1:
        last_status = status_changes[len(status_changes) - 1].status.status_id
        last_duration = timestamp - status_changes[len(status_changes) - 1].timestamp

        if last_status == 0:
            off_duration += last_duration
        elif last_status == 1:
            idle_duration += last_duration
        elif last_status == 2:
            production_duration += last_duration

    def timedelta_to_time(td):
        total_seconds = int(td.total_seconds())
        hours = (total_seconds // 3600) % 24
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return timett(hours, minutes, seconds)

    shift_summary.off_time = timedelta_to_time(off_duration)
    shift_summary.idle_time = timedelta_to_time(idle_duration)
    shift_summary.production_time = timedelta_to_time(production_duration)

    # print(f"\n## SHIFT SUMMARY: shift: {shift_id} | start: {shift_start_time}, end: {shift_end_time}\n"
    #       f"#  OFF_TIME: {shift_summary.off_time}\n"
    #       f"#   ON_TIME: {shift_summary.idle_time}\n"
    #       f"# PROD_TIME: {shift_summary.production_time}\n")

    # Update part counts
    shift_summary.total_parts = part_count
    if part_status == 2:  # Assuming 0 means good part
        shift_summary.good_parts = part_count
        shift_summary.bad_parts = 0
    else:
        shift_summary.bad_parts = part_count - shift_summary.good_parts
    # OEE Computation
    shift_summary.availability = (production_duration.total_seconds()/60)/(config_info.shift_duration
                                                                           - config_info.planned_non_production_time
                                                                           - config_info.planned_downtime)
    if production_duration.total_seconds()/60 != 0:
        shift_summary.performance = (shift_summary.total_parts * 1) / (production_duration.total_seconds()/60)

    if shift_summary.total_parts != 0:
        shift_summary.quality = shift_summary.good_parts / shift_summary.total_parts

    shift_summary.availability_loss = 1 - shift_summary.availability

    shift_summary.performance_loss = 1 - shift_summary.performance

    shift_summary.quality_loss = 1 - shift_summary.quality

    shift_summary.updatedate = datetime.now()

    commit()
    return shift_summary


def main_loop():
    """Main processing loop"""
    while True:
        try:
            # Get all machines
            with db_session:
                machines = get_all_machines()

            for machine in machines:
                try:
                    if machine.id in IGNORED_MACHINE_IDS:
                        continue
                    # Determine machine state
                    state_result = determine_machine_state(machine.id)

                    if state_result:
                        status_code, current_reading, timestamp = state_result

                        # Update machine status
                        status_changed = update_machine_status(machine.id, status_code, timestamp)

                        if status_changed:
                            print(f"Machine {machine.machine_name} (ID: {machine.id}) - "
                                  f"Current: {current_reading}A - "
                                  f"Status: {status_code} at {timestamp}")

                    manage_shift_summary(datetime.now(), machine.id)

                except Exception as e:
                    print(f"Error processing machine {machine.id}: {e}")

            # Wait before next check
            time.sleep(5)  # Check every 5 seconds

        except KeyboardInterrupt:
            print("Exiting...")
            break
        except Exception as e:
            print(f"Error in main loop: {e}")
            time.sleep(10)  # Longer wait on error


if __name__ == '__main__':
    try:
        # Connect to database
        connect_to_db()
        print("Database connected successfully")

        # print(get_machine_state_thresholds(5))
        # Start main processing loop
        main_loop()

    except Exception as e:
        print(f"Fatal error: {e}")

    # python -m app.services.oee_collector_ems