from datetime import datetime, timedelta
import pandas as pd
import time
from pony.orm import db_session, commit, desc, count
from tabulate import tabulate
import click
from app.database.connection import connect_to_db
from app.models import Program, PlannedScheduleItem, ScheduleVersion, ProductionLog, User
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

@db_session
def get_available_operators():
    """Get list of available operators"""
    try:
        # Get all users that are operators (assuming you have a user_type or similar field)
        operators = User.select()  # Get all users for now, you can filter based on your User model structure
        if not operators:
            print("No operators found!")
            return []
            
        return [(op.id, op.username) for op in operators]
    except Exception as e:
        print(f"Error getting operators: {str(e)}")
        return []

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
        """Get current job for a machine at the given time"""
        try:
            machine_schedule = self.schedule_df[self.schedule_df['machine'] == machine_id]
            current_job = machine_schedule[
                (machine_schedule['initial_start_time'] <= current_time) &
                (machine_schedule['initial_end_time'] > current_time)
            ]
            if not current_job.empty:
                operation = current_job['operation'].iloc[0]
                program_name = programs[operation]
                
                print(f"\nDebug: Processing job for machine {machine_id}")
                print(f"Operation: {operation}")
                print(f"Program Name: {program_name}")
                print(f"Total Quantity: {current_job['total_quantity'].iloc[0]}")
                print(f"Current Version: {current_job['current_version'].iloc[0]}")
                
                with db_session:
                    # Get program
                    program = Program.get(program_name=program_name)
                    if program:
                        # Get schedule item using current_version from CSV
                        schedule_version = ScheduleVersion.get(id=current_job['current_version'].iloc[0])
                        if schedule_version:
                            return {
                                'initial_start_time': current_job['initial_start_time'].iloc[0],
                                'initial_end_time': current_job['initial_end_time'].iloc[0],
                                'operation': operation,
                                'total_quantity': current_job['total_quantity'].iloc[0],
                                'remaining_quantity': current_job['remaining_quantity'].iloc[0],
                                'schedule_version_id': schedule_version.id
                            }
                        else:
                            print(f"No schedule version found with ID: {current_job['current_version'].iloc[0]}")
                    else:
                        print(f"Program not found: {program_name}")
                
            return None
        except Exception as e:
            print(f"Error in get_current_job: {str(e)}")
            return None

    def get_last_job_remaining_quantity(self, machine_id, operation):
        last_job = self.schedule_df[
            (self.schedule_df['machine'] == machine_id) &
            (self.schedule_df['operation'] == operation)
            ].sort_values(by='initial_end_time', ascending=False)
        # print(last_job.iloc[0]['remaining_quantity'])
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
            # print('$$$ ', schedule_version_id)
            return schedule_version_id.id

    @db_session
    def log_production(self, machine_id: int, schedule_version_id: int, operator_id: int, 
                      end_time: datetime, quantity_completed: int, quantity_rejected: int, notes: str):
        """Log production details with operator input"""
        try:
            print(f"\nDebug: Logging production for machine {machine_id}")
            print(f"Schedule Version ID: {schedule_version_id}")
            print(f"Operator ID: {operator_id}")
            
            # Get the schedule version
            schedule_version = ScheduleVersion.get(id=schedule_version_id)
            if not schedule_version:
                print(f"Error: Schedule version {schedule_version_id} not found")
                return

            # Get the operator
            operator = User.get(id=operator_id)
            if not operator:
                print(f"Error: Operator {operator_id} not found")
                return

            print(f"Found operator: {operator.username}")
            print(f"Found schedule version: {schedule_version.id}")

            # Create new production log
            new_log = ProductionLog(
                schedule_version=schedule_version,
                operator=operator,
                start_time=datetime.utcnow(),
                end_time=end_time,
                quantity_completed=quantity_completed,
                quantity_rejected=quantity_rejected,
                notes=notes
            )
            
            # Update the schedule version's completed quantity
            schedule_version.completed_quantity += quantity_completed
            schedule_version.remaining_quantity = max(0, schedule_version.remaining_quantity - quantity_completed)
            
            commit()
            print(f"Successfully logged production for machine {machine_id}")
            print(f"Completed: {quantity_completed}, Rejected: {quantity_rejected}")
            
        except Exception as e:
            print(f"Error logging production: {str(e)}")
            raise

    @db_session
    def get_operator_input(self, machine_id: int, schedule_version_id: int):
        """Get operator input for production logging"""
        try:
            # Show available operators
            operators = get_available_operators()
            if not operators:
                print("No operators found in the system!")
                return

            print("\nAvailable Operators:")
            for op_id, username in operators:
                print(f"{op_id}: {username}")

            # Get operator input with validation
            while True:
                try:
                    operator_id = click.prompt("Enter operator ID", type=int)
                    # Verify operator exists
                    with db_session:
                        operator = User.get(id=operator_id)
                        if operator:
                            break
                        print("Invalid operator ID. Please try again.")
                except click.Abort:
                    return
                except Exception:
                    print("Invalid input. Please enter a number.")

            # Get production details
            try:
                quantity_completed = click.prompt("Enter completed quantity", type=int)
                quantity_rejected = click.prompt("Enter rejected quantity", type=int)
                notes = click.prompt("Enter notes (optional)", type=str, default="")
                end_time = datetime.utcnow()

                # Log the production
                self.log_production(
                    machine_id=machine_id,
                    schedule_version_id=schedule_version_id,
                    operator_id=operator_id,
                    end_time=end_time,
                    quantity_completed=quantity_completed,
                    quantity_rejected=quantity_rejected,
                    notes=notes
                )

            except click.Abort:
                print("Production logging cancelled.")
                return
            except Exception as e:
                print(f"Error getting production details: {str(e)}")
                return

        except Exception as e:
            print(f"Error in operator input: {str(e)}")
            raise

    @db_session
    def simulate_machine_status(self, current_time):
        """Simulate machine status with operator input"""
        try:
            machine_statuses = {}
            state_changed = False

            for machine in self.unique_machines:
                current_job = self.get_current_job(machine, current_time)
                machine_raw_live = MachineRawLive.get(machine_id=int(machine))
                active_program = 'x'

                # Check if current_job exists (job running)
                if current_job:
                    total_duration = (current_job['initial_end_time'] - current_job['initial_start_time']).total_seconds()
                    elapsed_time = (current_time - current_job['initial_start_time']).total_seconds()
                    progress = min(1.0, elapsed_time / total_duration)

                    machine_state = StatusLookup.get(status_name="PRODUCTION")
                    last_remaining_quantity = self.get_last_job_remaining_quantity(machine, current_job['operation'])

                    if (current_job['remaining_quantity'] == 0 and last_remaining_quantity is not None
                            and last_remaining_quantity != 0):
                        part_count = current_job['total_quantity'] - last_remaining_quantity + int(
                            progress * last_remaining_quantity)
                    else:
                        part_count = int(progress * (current_job['total_quantity'] - current_job['remaining_quantity']))

                    active_program = programs[current_job['operation']]
                    job_in_progress = 0
                    program_number = str(current_job['operation'])
                else:
                    last_status_entry = MachineRaw.select(lambda m: m.machine_id == int(machine)).order_by(
                        desc(MachineRaw.time_stamp)).first()

                    if last_status_entry and last_status_entry.status.status_name == "PRODUCTION":
                        machine_state = StatusLookup.get(status_name="ON")
                        job_in_progress = 0
                        part_count = last_status_entry.part_count + 1
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
                jip = self.get_schedule_version_id(machine_id=int(machine), program=active_program) if active_program != 'x' else None

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

                live_entry = MachineRawLive.get(machine_id=int(machine))
                if live_entry:
                    live_entry.time_stamp = current_time
                    live_entry.status = machine_state
                    live_entry.job_in_progress = jip
                    if not part_count == 0:
                        live_entry.part_count = part_count
                else:
                    MachineRawLive(
                        machine_id=int(machine),
                        time_stamp=current_time,
                        status=machine_state,
                        active_program=active_program,
                        part_count=part_count,
                        job_in_progress=jip,
                        job_status=0
                    )

                # When a job is completed (current_job is None but was running before)
                was_running = self.current_jobs[machine] is not None
                if not current_job and was_running:
                    previous_job = self.current_jobs[machine]
                    if previous_job and 'schedule_version_id' in previous_job:
                        if click.confirm(f"\nJob completed on machine {machine}. Log production details?"):
                            try:
                                self.get_operator_input(
                                    machine_id=int(machine),
                                    schedule_version_id=previous_job['schedule_version_id']
                                )
                            except Exception as e:
                                print(f"Error logging production for machine {machine}: {str(e)}")
                    else:
                        print(f"Warning: No schedule version ID found for previous job on machine {machine}")

                self.current_jobs[machine] = current_job

            if state_changed:
                self.print_machine_status(machine_statuses, current_time)
            
        except Exception as e:
            print(f"Error in simulate_machine_status: {str(e)}")
            raise

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

    def debug_csv_contents(self):
        """Debug function to print CSV contents"""
        print("\nCSV File Contents:")
        print(self.schedule_df.head())
        print("\nUnique operations:", self.schedule_df['operation'].unique())
        print("Unique machines:", self.schedule_df['machine'].unique())

@db_session
def debug_program_lookup(operation_id):
    """Debug function to check program existence"""
    program_name = programs[operation_id]
    print(f"\nDebug: Looking for program: {program_name}")
    
    program = Program.select(lambda p: p.program_name == program_name).first()
    if program:
        print(f"Found program: {program.program_name}")
        print(f"Operation: {program.operation.operation_description}")
        return program
    else:
        print("Program not found in database!")
        return None

@db_session
def debug_schedule_items(program):
    """Debug function to check schedule items"""
    if not program:
        return
    
    items = PlannedScheduleItem.select(
        lambda x: x.operation == program.operation
    )
    print(f"\nFound {len(items)} schedule items for operation {program.operation.operation_description}")
    for item in items:
        print(f"Schedule Item ID: {item.id}")
        print(f"Total Quantity: {item.total_quantity}")
        print(f"Remaining Quantity: {item.remaining_quantity}")
        print(f"Order: {item.order.production_order}")
        
        # Check schedule versions
        versions = ScheduleVersion.select(lambda v: v.schedule_item == item)
        print(f"Found {len(versions)} schedule versions")
        for version in versions:
            print(f"Version ID: {version.id}")
            print(f"Is Active: {version.is_active}")
            print(f"Planned Quantity: {version.planned_quantity}")
            print("---")

@db_session
def ensure_test_operator():
    """Ensure there's at least one test operator in the system"""
    try:
        test_operator = User.get(username='test_operator')
        if not test_operator:
            test_operator = User(
                username='test_operator',
                email='test@example.com',
                # Add other required fields based on your User model
            )
            commit()
            print("Created test operator")
        return test_operator
    except Exception as e:
        print(f"Error creating test operator: {str(e)}")
        return None

def main():
    connect_to_db()
    
    # Ensure test operator exists
    ensure_test_operator()
    
    # Use the new schedule file
    simulator = MachineSimulator('app/simulator/planned_schedule_items_2.csv')
    
    # Debug CSV contents
    simulator.debug_csv_contents()
    
    # Debug database state
    with db_session:
        print("\nDatabase State:")
        print(f"Programs: {count(p for p in Program)}")
        print(f"Schedule Items: {count(s for s in PlannedScheduleItem)}")
        print(f"Schedule Versions: {count(v for v in ScheduleVersion)}")
        print(f"Status Lookups: {count(s for s in StatusLookup)}")
        print(f"Users: {count(u for u in User)}")
    
    start_time = datetime(2025, 1, 21, 11, 41)
    simulator.run_simulation(start_time=start_time, speed_factor=60)

if __name__ == "__main__":
    main()
