from datetime import datetime, timedelta
import pandas as pd
import time
from pony.orm import db_session, commit, desc
from tabulate import tabulate

from app.database.connection import connect_to_db
from app.models import Program, PlannedScheduleItem, ScheduleVersion, ProductionLog
from app.models.production import MachineRaw, StatusLookup, MachineRawLive

status_dict = {'OFF': 0, 'ON': 1, 'PRODUCTION': 2}
programs = {
    34: 'MMM3-213301840171-OP20',
    35: 'CNCM-213301840171-OP50',
    36: 'QFAB-213301840171-OP80',
    37: 'NEWC-213301840171-OP60',
    38: 'CNCM-213301840171-OP40',
    39: 'MMC1-213301840171-OP10',
    40: 'CNCM-213301840171-OP30',
    41: 'QFAB-213301840171-OP90',
    42: 'SMFD-213301840171-OP70',
    43: 'MMC1-213301840171-OP10',  # Add any additional operations from your schedule
    44: 'MMM3-213301840171-OP20',
    45: 'CNCM-213301840171-OP50'
}

class MachineSimulator:
    def __init__(self, schedule_file):
        self.schedule_df = pd.read_csv(schedule_file)
        self.schedule_df = self.schedule_df[self.schedule_df['total_quantity'] != 1]
        self.schedule_df['initial_start_time'] = pd.to_datetime(self.schedule_df['initial_start_time'], format='mixed')
        self.schedule_df['initial_end_time'] = pd.to_datetime(self.schedule_df['initial_end_time'], format='mixed')

        self.machine_states = {}
        self.current_jobs = {}
        self.last_machine_status = {}
        self.unique_machines = self.schedule_df['machine'].unique()

        for machine in self.unique_machines:
            self.machine_states[machine] = 0
            self.last_machine_status[machine] = 0
            self.current_jobs[machine] = None

        self.current_id = 1
        self.first_print = True
        self.last_printed_status = {}

    def get_current_job(self, machine_id, current_time):
        machine_schedule = self.schedule_df[self.schedule_df['machine'] == machine_id]
        current_job = machine_schedule[
            (machine_schedule['initial_start_time'] <= current_time) &
            (machine_schedule['initial_end_time'] > current_time)
            ]
        return current_job.iloc[0] if not current_job.empty else None

    def get_last_job_remaining_quantity(self, machine_id, operation):
        last_job = self.schedule_df[
            (self.schedule_df['machine'] == machine_id) &
            (self.schedule_df['operation'] == operation)
            ].sort_values(by='initial_end_time', ascending=False)
        if len(last_job) > 1:
            return last_job.iloc[1]['remaining_quantity'] if not last_job.empty else None

    @db_session
    def get_schedule_version_id(self, machine_id, program):
        corresponding_program = Program.select(lambda x: x.program_name == program).first()
        if corresponding_program:
            corresponding_operation = corresponding_program.operation
            corresponding_order = corresponding_program.operation.order
            planned_schedule_item = PlannedScheduleItem.select(
                lambda x: x.order == corresponding_order and x.operation == corresponding_operation
                          and x.total_quantity != 1).first()
            schedule_version_id = ScheduleVersion.select(lambda x: x.schedule_item == planned_schedule_item).first()
            return schedule_version_id.id

    @db_session
    def simulate_machine_status(self, current_time):
        machine_statuses = {}
        state_changed = False

        for machine in self.unique_machines:
            machine_live = MachineRawLive.get(machine_id=int(machine))
            current_job = self.get_current_job(machine, current_time)
            machine_raw_live = MachineRawLive.get(machine_id=int(machine))
            active_program = 'x'

            last_status_entry = MachineRaw.select(lambda m: m.machine_id == int(machine)).order_by(
                desc(MachineRaw.time_stamp)).first()

            if current_job is not None:
                total_duration = (current_job['initial_end_time'] - current_job['initial_start_time']).total_seconds()
                elapsed_time = (current_time - current_job['initial_start_time']).total_seconds()
                progress = min(1.0, elapsed_time / total_duration)

                machine_state = StatusLookup.get(status_name="PRODUCTION")
                last_remaining_quantity = self.get_last_job_remaining_quantity(machine, current_job['operation'])

                if (current_job['remaining_quantity'] == 0 and last_remaining_quantity is not None
                        and last_remaining_quantity != 0):
                    # print(last_remaining_quantity)
                    part_count = current_job['total_quantity'] - last_remaining_quantity + int(
                        progress * last_remaining_quantity)
                else:
                    part_count = int(progress * (current_job['total_quantity'] - current_job['remaining_quantity']))

                # print( f"### {current_job['total_quantity']} - {current_job['remaining_quantity']} -> {part_count}
                # || PROGRESS: {progress}")
                active_program = programs[current_job['operation']]
                job_in_progress = 0
                program_number = str(current_job['operation'])
            else:

                if last_status_entry and last_status_entry.status.status_name == "PRODUCTION":
                    machine_state = StatusLookup.get(status_name="ON")
                    job_in_progress = 0
                    part_count = last_status_entry.part_count + 1

                    pending_log = ProductionLog.select(
                        lambda p: p.machine_id == int(machine) and
                                  p.start_time is not None and
                                  p.end_time is None
                    ).order_by(lambda p: desc(p.start_time)).first()
                    pending_log.end_time = current_time
                    pending_log.quantity_completed = part_count
                    pending_log.quantity_rejected = 0
                    machine_live.job_status = 0

                else:
                    machine_state = StatusLookup.get(status_name="ON")
                    job_in_progress = 0
                    part_count = 0

            machine_statuses[machine] = {
                "Status": machine_state.status_name,
                "Active_Program": active_program,
                "Part Count": part_count
            }

            existing_entry = MachineRaw.get(machine_id=int(machine), time_stamp=current_time)
            jip = self.get_schedule_version_id(machine_id=int(machine), program=active_program)

            # self.insert_into_production_logs()

            if existing_entry:
                if (existing_entry.status != machine_state or
                        existing_entry.job_in_progress != job_in_progress or
                        existing_entry.program_number != program_number or
                        existing_entry.part_count != part_count):
                    existing_entry.status = machine_state
                    existing_entry.job_in_progress = job_in_progress
                    existing_entry.program_number = program_number
                    existing_entry.part_count = part_count
                    state_changed = True
            else:
                if self.last_machine_status[machine] != machine_statuses[machine]:
                    MachineRaw(
                        machine_id=int(machine),
                        time_stamp=current_time,
                        status=machine_state,
                        active_program=active_program,
                        part_count=part_count,
                        job_in_progress=jip
                    )
                    state_changed = True
                    self.last_machine_status[machine] = machine_statuses[machine]

                    if jip:
                        if machine_live.job_status == 0:
                            ProductionLog(
                                machine_id=int(machine),
                                schedule_version=jip,
                                start_time=current_time
                            )
                            machine_live.job_status = 1

            live_entry = MachineRawLive.get(machine_id=int(machine))
            if live_entry:
                live_entry.timestamp = current_time
                live_entry.status = machine_state
                live_entry.job_in_progress = jip
                if not part_count == 0:
                    live_entry.part_count = part_count
            else:
                MachineRawLive(
                    machine_id=int(machine),
                    timestamp=current_time,
                    status=machine_state,
                    active_program=active_program,
                    part_count=part_count,
                    job_in_progress=jip,
                    job_status=0
                )

        commit()

        if state_changed:
            self.print_machine_status(machine_statuses, current_time)

    def print_machine_status(self, machine_statuses, current_time):
        headers = ["Timestamp"] + list(machine_statuses.keys())
        values = [f"{status['Status']}, {status['Part Count']}" for status in machine_statuses.values()]

        if self.last_printed_status != values:
            print("\n" + tabulate([[current_time] + values], headers=headers, tablefmt="grid"))
            self.last_printed_status = values

    @db_session
    def initialize_tables(self, start_time):
        # Truncate the machine_raw table and restart identity
        MachineRaw._database_.execute("TRUNCATE TABLE production.machine_raw RESTART IDENTITY CASCADE;")
        ProductionLog._database_.execute("TRUNCATE TABLE scheduling.production_logs RESTART IDENTITY CASCADE;")

        # Reset columns in machine_raw_live except for machine_id using ORM update
        for live_entry in MachineRawLive.select():
            live_entry.time_stamp = start_time
            live_entry.status = 0
            live_entry.job_in_progress = 0
            live_entry.job_status = 0

        commit()

    def run_simulation(self, start_time=None, speed_factor=1):
        """Run the simulation from start_time (or earliest schedule time) at the specified speed"""
        self.initialize_tables(start_time)

        if start_time is None:
            start_time = self.schedule_df['initial_start_time'].min()

        end_time = self.schedule_df['initial_end_time'].max()
        current_time = start_time

        while current_time <= end_time:
            # Generate and save simulation data for current timestamp
            self.simulate_machine_status(current_time)

            # Increment time (1 minute intervals by default)
            current_time += timedelta(minutes=1)

            # Sleep to control simulation speed
            time.sleep(1 / speed_factor)


if __name__ == "__main__":
    connect_to_db()

    # Initialize simulator
    simulator = MachineSimulator('app/simulator/planned_schedule_items_2.csv')

    # Set simulation start time
    start_time = datetime(2025, 1, 21, 11, 41)

    # Run simulation at 60x speed (1 minute = 1 second)
    simulator.run_simulation(start_time=start_time, speed_factor=240)


    # python -m app.simulator.test
