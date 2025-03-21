from datetime import datetime, timedelta
import pandas as pd
import time
from pony.orm import db_session, commit, desc
from tabulate import tabulate

from app.database.connection import connect_to_db
from app.models import Program, PlannedScheduleItem, ScheduleVersion, ProductionLog
from app.models.production import MachineRaw, StatusLookup, MachineRawLive, ShiftInfo, ShiftSummary, ConfigInfo

import time
from datetime import datetime, timezone, timedelta
from datetime import time as timett

from opcua import Client, ua
from pony.orm import db_session, commit, desc, select

# OPC UA Server connection details
server_url = "opc.tcp://172.18.28.35:4840"
username = "OpcUaClient"
password = "12345678"

client = Client(server_url)
client.set_user(username)
client.set_password(password)

STATUS_MAPPING = {
    "OFF": 0,
    "IDLE": 1,
    "PRODUCTION": 2
}


def is_client_connected():
    """Check if client is properly connected"""
    try:
        if client.uaclient and client.uaclient._uasocket:
            return client.uaclient._uasocket._thread.is_alive()
        return False
    except:
        return False


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


def safe_disconnect():
    """Safely disconnect from OPC UA server"""
    try:
        if is_client_connected():
            client.disconnect()
            print("Disconnected from OPC UA Server")
    except Exception as e:
        print(f"Error during OPC UA disconnect (can be ignored if already disconnected): {e}")


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

    config_info = ConfigInfo.get(machine_id=machine_id)

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

    status_changes = [s for s in status_changes if s is not None]
    status_changes = sorted(status_changes, key=lambda x: x.timestamp)

    if len(status_changes) == 1:
        duration = timestamp - shift_start
        if status_changes[0].status.status_id == STATUS_MAPPING["OFF"]:
            off_duration += duration
        elif status_changes[0].status.status_id == STATUS_MAPPING["IDLE"]:
            idle_duration += duration
        elif status_changes[0].status.status_id == STATUS_MAPPING["PRODUCTION"]:
            production_duration += duration
    else:
        for i in range(1, len(status_changes)):
            if i == 1:
                start_time = max(shift_start, status_changes[i - 1].timestamp)
            else:
                start_time = status_changes[i - 1].timestamp

            end_time = status_changes[i].timestamp

            duration = end_time - start_time

            if status_changes[i - 1].status.status_id == STATUS_MAPPING["OFF"]:
                off_duration += duration
            elif status_changes[i - 1].status.status_id == STATUS_MAPPING["IDLE"]:
                idle_duration += duration
            elif status_changes[i - 1].status.status.status_id == STATUS_MAPPING["PRODUCTION"]:
                production_duration += duration

    if len(status_changes) > 1:
        last_status = status_changes[len(status_changes) - 1].status.status_id
        last_duration = timestamp - status_changes[len(status_changes) - 1].timestamp

        if last_status == STATUS_MAPPING["OFF"]:
            off_duration += last_duration
        elif last_status == STATUS_MAPPING["IDLE"]:
            idle_duration += last_duration
        elif last_status == STATUS_MAPPING["PRODUCTION"]:
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
    print(f"\n## SHIFT SUMMARY: shift: {shift_id} | start: {shift_start_time}, end: {shift_end_time}\n"
          f"#  OFF_TIME: {shift_summary.off_time}\n"
          f"#   ON_TIME: {shift_summary.idle_time}\n"
          f"# PROD_TIME: {shift_summary.production_time}\n")
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

    return shift_summary


@db_session
def database_insertion(server_timestamp, operation_mode, machine_status, machine_id, part_count=0, part_status=2):
    """
    Insert machine signals into both historical and active signal pools
    Also manages shift summaries
    """
    try:
        timestamp = datetime.strptime(server_timestamp, '%Y-%m-%d %H:%M:%S')
        status_code = STATUS_MAPPING.get(machine_status, 0)

        active_signal = MachineRawLive.get(machine_id=machine_id)
        print(active_signal.timestamp)
        if active_signal is None or (
                active_signal.op_mode != operation_mode or
                active_signal.status.status_id != status_code or
                active_signal.part_count != part_count
        ):
            MachineRaw(
                timestamp=timestamp,
                machine_id=machine_id,
                op_mode=operation_mode,
                status=status_code,
                part_count=part_count,
                part_status=part_status
            )

            print(f'STATUS CHANGE => {timestamp} >> '
                  f'Machine ID: {machine_id} | '
                  f'Status: {status_code} | '
                  f'Operation Mode: {operation_mode} | '
                  f'Part Count: {part_count} | '
                  f'Part Status: {part_status}')

        if active_signal:
            active_signal.timestamp = timestamp
            active_signal.op_mode = operation_mode
            active_signal.status = status_code
            active_signal.part_count = part_count
        else:
            MachineRawLive(
                timestamp=timestamp,
                machine_id=machine_id,
                op_mode=operation_mode,
                status=status_code,
                part_count=part_count
            )

        manage_shift_summary(timestamp, machine_id, part_count, part_status)
        commit()
    except ValueError as ve:
        print(f'Error parsing timestamp: {ve}')
    except Exception as e:
        print(f'Exception during insertion: {e}')


@db_session
def handle_disconnection(machine_id=1):
    recent_status = select(s for s in MachineRaw if s.machine_id == machine_id) \
        .order_by(lambda s: desc(s.timestamp)) \
        .first()

    current_time = datetime.now()

    if not recent_status or recent_status.status != STATUS_MAPPING["OFF"]:
        MachineRaw(
            timestamp=current_time,
            machine_id=machine_id,
            op_mode=-1,
            status=STATUS_MAPPING["OFF"]
        )

        active_signal = MachineRawLive.get(machine_id=machine_id)
        if active_signal:
            active_signal.timestamp = current_time
            active_signal.op_mode = -1
            active_signal.status = STATUS_MAPPING["OFF"]
        else:
            MachineRawLive(
                timestamp=current_time,
                machine_id=machine_id,
                op_mode=-1,
                status=STATUS_MAPPING["OFF"]
            )
    else:
        active_signal = MachineRawLive.get(machine_id=machine_id)
        if active_signal:
            active_signal.timestamp = current_time
        manage_shift_summary(current_time, machine_id)

    commit()


if __name__ == '__main__':
    try:
        connect_to_db()
        print("Database Binded Successfully")
    except Exception as e:
        print(f"Error generating mapping: {e}")
        exit(1)

    retry_delay = 60  # seconds

    while True:
        try:
            client.connect()
            print("Connected to OPC UA Server")
            while True:
                try:
                    operation_mode = -1
                    machine_status = "IDLE"

                    if is_client_connected():
                        try:
                            operation_mode = int(client.get_node("ns=2;s=/Bag/State/opMode").get_value())
                            server_timestamp = datetime.fromisoformat(
                                str(client.get_node("i=2258").get_value())).replace(
                                tzinfo=timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S')
                            cycle_start = client.get_node("ns=2;s=/Plc/Q113.5").get_value()

                            for i in range(10):
                                if not client.get_node("ns=2;s=/Plc/Q113.5").get_value():
                                    break
                                else:
                                    time.sleep(0.1)
                            else:
                                if operation_mode == 2:
                                    machine_status = "PRODUCTION"
                                else:
                                    machine_status = "IDLE"
                        except ua.UaStatusCodeError as ua_err:
                            print(f"OPC UA read error: {ua_err}")
                            machine_status = "OFF"
                            raise
                    else:
                        machine_status = "OFF"
                        server_timestamp = str(datetime.now())
                        raise ConnectionError("Client disconnected")

                    part_count = int(client.get_node("ns=2;s=/Channel/State/actParts").get_value())

                    database_insertion(server_timestamp, operation_mode, machine_status, 1, part_count, 0)
                    time.sleep(1)

                except (ua.UaStatusCodeError, ConnectionError) as e:
                    print(f"Connection issue detected: {e}")
                    database_insertion(str(datetime.now()), -1, "OFF", 1)
                    raise
        except Exception as e:
            print(e)
            safe_disconnect()
            handle_disconnection()
            time.sleep(retry_delay)
        finally:
            safe_disconnect()
            print("Disconnected from Database")
