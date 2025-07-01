from datetime import datetime, timedelta
from typing import Dict, Set, Literal, Optional, List
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request, HTTPException, Query, Path
from fastapi.responses import StreamingResponse, JSONResponse
import asyncio
import json
from pony.orm import db_session, select, desc

from app.database.connection import db
from app.models.energymonitoring import MachineEMSLive, MachineEMSHistory, ShiftwiseEnergyLive, ShiftwiseEnergyHistory
from app.models import Machine
from collections import defaultdict
from pydantic import BaseModel
from enum import Enum
import math

router = APIRouter(prefix="/api/v1/energy-monitoring", tags=["energy-monitoring"])


# Global connection managers for SSE clients
class SSEConnectionManager:
    def __init__(self):
        self.active_connections: Set[asyncio.Queue] = set()

    async def connect(self) -> asyncio.Queue:
        queue = asyncio.Queue()
        self.active_connections.add(queue)
        print(f"New client connected. Total clients: {len(self.active_connections)}")
        return queue

    def disconnect(self, queue: asyncio.Queue):
        self.active_connections.remove(queue)
        print(f"Client disconnected. Total clients: {len(self.active_connections)}")

    async def broadcast(self, data: str):
        if self.active_connections:
            for queue in self.active_connections:
                await queue.put(data)


# Separate connection managers for different endpoints
status_connection_manager = SSEConnectionManager()
parameter_connection_manager = SSEConnectionManager()
machine_parameter_managers = defaultdict(SSEConnectionManager)  # Key: (machine_id, 'parameters')
history_connection_manager = defaultdict(SSEConnectionManager)  # Key: (machine_id, parameter)
shiftwise_energy_manager = SSEConnectionManager()  # For shiftwise energy data


class MachineStatusTracker:
    def __init__(self):
        self.previous_states = {}
        self.NUMERIC_THRESHOLD = 0.0001
        self.last_broadcast_time = {}  # Track last broadcast time per machine
        self.MIN_BROADCAST_INTERVAL = 1.0  # Minimum seconds between broadcasts for the same machine

    def _is_significant_change(self, curr_value, prev_value):
        """Helper method to determine if a change is significant enough to broadcast"""
        if curr_value is None or prev_value is None:
            return curr_value != prev_value

        # Handle numeric comparisons
        if isinstance(curr_value, (int, float)) and isinstance(prev_value, (int, float)):
            return abs(curr_value - prev_value) > self.NUMERIC_THRESHOLD

        # Handle string comparisons (case-sensitive)
        if isinstance(curr_value, str) and isinstance(prev_value, str):
            return curr_value != prev_value

        # Handle boolean comparisons
        if isinstance(curr_value, bool) and isinstance(prev_value, bool):
            return curr_value != prev_value

        # Handle lists/arrays (if needed)
        if isinstance(curr_value, (list, tuple)) and isinstance(prev_value, (list, tuple)):
            if len(curr_value) != len(prev_value):
                return True
            return any(self._is_significant_change(c, p) for c, p in zip(curr_value, prev_value))

        # Default comparison for other types
        return curr_value != prev_value

    def detect_changes(self, current_data):
        """
        Detects if there are any status changes and returns only changed machines.
        Returns None if no changes, or a list of changed machine statuses if there are changes.
        Implements rate limiting and sophisticated change detection.
        """
        if not current_data:
            return None

        current_time = datetime.now()
        changed_machines = []
        current_states = {str(machine['machine_id']): machine for machine in current_data}

        # Check for changes in existing machines
        for machine_id, current_state in current_states.items():
            previous_state = self.previous_states.get(machine_id)

            # If no previous state, it's a new machine
            if not previous_state:
                changed_machines.append(current_state)
                continue

            # Check if enough time has passed since last broadcast
            last_broadcast = self.last_broadcast_time.get(machine_id, datetime.min)
            if (current_time - last_broadcast).total_seconds() < self.MIN_BROADCAST_INTERVAL:
                continue

            # Check each field for significant changes
            has_changes = False
            for key, curr_value in current_state.items():
                prev_value = previous_state.get(key)
                if self._is_significant_change(curr_value, prev_value):
                    has_changes = True
                    break

            if has_changes:
                changed_machines.append(current_state)
                self.last_broadcast_time[machine_id] = current_time

        # Check for machines that were removed
        for machine_id in list(self.previous_states.keys()):
            if machine_id not in current_states:
                # Machine was removed
                changed_machines.append({
                    "machine_id": machine_id,
                    "status": "OFFLINE",
                    "timestamp": current_time.isoformat()
                })

        # Update previous states with current states
        self.previous_states = current_states

        return changed_machines if changed_machines else None


class MachineParameterTracker:
    def __init__(self):
        self.previous_states = {}

    def detect_parameter_changes(self, current_data):
        """
        Detects if there are any parameter changes and returns only changed machines.
        Returns None if no changes, or a list of changed machine parameters if there are changes.
        """
        changed_machines = []

        # Create a map of current machine states
        current_states = {machine["machine_id"]: machine for machine in current_data}

        # Parameters to monitor for changes (excluding timestamp)
        parameters_to_monitor = [
            'phase_a_voltage', 'phase_b_voltage', 'phase_c_voltage',
            'avg_phase_voltage', 'line_ab_voltage', 'line_bc_voltage',
            'line_ca_voltage', 'avg_line_voltage', 'phase_a_current',
            'phase_b_current', 'phase_c_current', 'avg_three_phase_current',
            'power_factor', 'frequency', 'total_instantaneous_power',
            'active_energy_delivered', 'status'
        ]

        # Check for new machines or parameter changes
        for machine_id, current_state in current_states.items():
            prev_state = self.previous_states.get(machine_id)

            if prev_state is None:
                # New machine, include it
                changed_machines.append(current_state)
            else:
                # Check each parameter for changes
                has_changes = False
                for param in parameters_to_monitor:
                    curr_value = current_state.get(param)
                    prev_value = prev_state.get(param)

                    # Handle numeric comparisons to avoid floating point issues
                    if isinstance(curr_value, (int, float)) and isinstance(prev_value, (int, float)):
                        if abs(curr_value - prev_value) > 0.0001:  # Small threshold for float comparison
                            has_changes = True
                            break
                    # Handle non-numeric comparisons
                    elif curr_value != prev_value:
                        has_changes = True
                        break

                if has_changes:
                    changed_machines.append(current_state)

        # Check for machines that were removed
        for machine_id in list(self.previous_states.keys()):
            if machine_id not in current_states:
                # Machine was removed
                changed_machines.append({
                    "machine_id": machine_id,
                    "status": "OFFLINE",
                    "timestamp": datetime.now().isoformat()
                })

        # Update previous states with current states
        self.previous_states = current_states

        return changed_machines if changed_machines else None


status_tracker = MachineStatusTracker()
parameter_tracker = MachineParameterTracker()


async def get_all_machine_statuses():
    """Helper function to get status of all machines"""
    with db_session:
        machines = select(m for m in Machine)[:]
        machine_dict = {m.id: f"{m.work_center.code}-{m.make}" if hasattr(m, 'work_center') else f"Machine-{m.id}"
                        for m in machines}

        live_statuses = select(e for e in MachineEMSLive)[:]
        status_data = []

        for status in live_statuses:
            machine_name = machine_dict.get(status.machine_id, f"Machine-{status.machine_id}")
            status_data.append({
                "machine_id": status.machine_id,
                "machine_name": machine_name,
                "status": status.status,
                "timestamp": status.timestamp.isoformat(),
                "total_power": status.total_instantaneous_power,
                "energy_consumed": status.active_energy_delivered
            })

        return status_data


async def get_all_machine_parameters():
    """Helper function to get all parameters of all machines"""
    with db_session:
        machines = select(m for m in Machine)[:]
        machine_dict = {m.id: f"{m.work_center.code}-{m.make}" if hasattr(m, 'work_center') else f"Machine-{m.id}"
                        for m in machines}

        live_statuses = select(e for e in MachineEMSLive)[:]
        parameter_data = []

        for status in live_statuses:
            machine_name = machine_dict.get(status.machine_id, f"Machine-{status.machine_id}")
            parameter_data.append({
                "machine_id": status.machine_id,
                "machine_name": machine_name,
                "status": status.status,
                "timestamp": status.timestamp.isoformat(),
                "phase_a_voltage": status.phase_a_voltage,
                "phase_b_voltage": status.phase_b_voltage,
                "phase_c_voltage": status.phase_c_voltage,
                "avg_phase_voltage": status.avg_phase_voltage,
                "line_ab_voltage": status.line_ab_voltage,
                "line_bc_voltage": status.line_bc_voltage,
                "line_ca_voltage": status.line_ca_voltage,
                "avg_line_voltage": status.avg_line_voltage,
                "phase_a_current": status.phase_a_current,
                "phase_b_current": status.phase_b_current,
                "phase_c_current": status.phase_c_current,
                "avg_three_phase_current": status.avg_three_phase_current,
                "power_factor": status.power_factor,
                "frequency": status.frequency,
                "total_instantaneous_power": status.total_instantaneous_power,
                "active_energy_delivered": status.active_energy_delivered
            })

        return parameter_data


async def monitor_and_broadcast_status_changes():
    """Background task to monitor status changes and broadcast to all status clients"""
    while True:
        try:
            current_data = await get_all_machine_statuses()
            changed_machines = status_tracker.detect_changes(current_data)

            if changed_machines:
                # Broadcast to all connected status clients
                await status_connection_manager.broadcast(f"data: {json.dumps(changed_machines)}\n\n")

            await asyncio.sleep(1)
        except Exception as e:
            print(f"Error in status monitor task: {str(e)}")
            await asyncio.sleep(1)


async def monitor_and_broadcast_parameter_changes():
    """Background task to monitor parameter changes and broadcast to all parameter clients"""
    while True:
        try:
            current_data = await get_all_machine_parameters()
            changed_machines = parameter_tracker.detect_parameter_changes(current_data)

            if changed_machines:
                # Broadcast to all connected parameter clients
                await parameter_connection_manager.broadcast(f"data: {json.dumps(changed_machines)}\n\n")

            await asyncio.sleep(1)
        except Exception as e:
            print(f"Error in parameter monitor task: {str(e)}")
            await asyncio.sleep(1)


async def status_client_event_generator(request: Request):
    """Generator for individual status client SSE events"""
    client_queue = None
    try:
        client_queue = await status_connection_manager.connect()

        # Send initial state
        initial_data = await get_all_machine_statuses()
        yield f"data: {json.dumps(initial_data)}\n\n"

        # Listen for updates
        while True:
            try:
                data = await client_queue.get()
                if data is None:  # Check for shutdown signal
                    break
                yield data
            except asyncio.CancelledError:
                print("Client connection was cancelled")
                break
            except Exception as e:
                print(f"Error in status client generator: {str(e)}")
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                await asyncio.sleep(1)  # Brief pause before retrying
    except Exception as e:
        print(f"Fatal error in status client generator: {str(e)}")
        if client_queue:
            yield f"event: error\ndata: {json.dumps({'error': 'Connection error, please refresh'})}\n\n"
    finally:
        if client_queue:
            await client_queue.put(None)  # Signal shutdown
            status_connection_manager.disconnect(client_queue)
            print("Cleaned up status client connection")


async def parameter_client_event_generator(request: Request):
    """Generator for individual parameter client SSE events"""
    client_queue = None
    try:
        client_queue = await parameter_connection_manager.connect()

        # Send initial state
        initial_data = await get_all_machine_parameters()
        yield f"data: {json.dumps(initial_data)}\n\n"

        # Listen for updates
        while True:
            try:
                data = await client_queue.get()
                if data is None:  # Check for shutdown signal
                    break
                yield data
            except asyncio.CancelledError:
                print("Client connection was cancelled")
                break
            except Exception as e:
                print(f"Error in parameter client generator: {str(e)}")
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                await asyncio.sleep(1)  # Brief pause before retrying
    except Exception as e:
        print(f"Fatal error in parameter client generator: {str(e)}")
        if client_queue:
            yield f"event: error\ndata: {json.dumps({'error': 'Connection error, please refresh'})}\n\n"
    finally:
        if client_queue:
            await client_queue.put(None)  # Signal shutdown
            parameter_connection_manager.disconnect(client_queue)
            print("Cleaned up parameter client connection")


@router.get("/machine-status-stream")
async def stream_machine_status(request: Request):
    """
    Server-Sent Events (SSE) endpoint for real-time machine status updates.
    Supports multiple clients connecting simultaneously.
    Only sends basic status information (status, power, energy).
    """
    # Start the monitoring task if it's not already running
    task = asyncio.create_task(monitor_and_broadcast_status_changes())

    return StreamingResponse(
        status_client_event_generator(request),
        media_type="text/event-stream",
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


@router.get("/machine-parameters-stream")
async def stream_machine_parameters(request: Request):
    """
    Server-Sent Events (SSE) endpoint for real-time machine parameter updates.
    Supports multiple clients connecting simultaneously.
    Sends detailed parameter information (voltages, currents, power factors, etc.).
    """
    # Start the monitoring task if it's not already running
    task = asyncio.create_task(monitor_and_broadcast_parameter_changes())

    return StreamingResponse(
        parameter_client_event_generator(request),
        media_type="text/event-stream",
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


class SingleMachineParameterTracker:
    def __init__(self, machine_id: int):
        self.machine_id = machine_id
        self.previous_state = None
        self.NUMERIC_THRESHOLD = 0.0001
        self.last_broadcast_time = datetime.min

    def _is_significant_change(self, curr_value, prev_value):
        """Helper method to determine if a change is significant enough to broadcast"""
        if curr_value is None or prev_value is None:
            return curr_value != prev_value

        # Handle numeric comparisons
        if isinstance(curr_value, (int, float)) and isinstance(prev_value, (int, float)):
            return abs(curr_value - prev_value) > self.NUMERIC_THRESHOLD

        return curr_value != prev_value

    def detect_changes(self, current_data):
        """Detects if there are any parameter changes for the specific machine"""
        if not current_data:
            return None

        current_time = datetime.now()

        # If no previous state, consider it as changed
        if self.previous_state is None:
            self.previous_state = current_data
            return current_data

        # Check each parameter for changes
        has_changes = False
        for key, curr_value in current_data.items():
            if key == 'timestamp':  # Skip timestamp comparison
                continue
            prev_value = self.previous_state.get(key)
            if self._is_significant_change(curr_value, prev_value):
                has_changes = True
                break

        # Update previous state and return if changed
        if has_changes:
            self.previous_state = current_data
            return current_data
        return None


async def get_single_machine_parameters(machine_id: int):
    """Helper function to get parameters of a specific machine"""
    with db_session:
        # Get machine name from Machine table
        machine = Machine.get(id=machine_id)
        machine_name = machine.make if machine else f"Unknown Machine {machine_id}"

        live_status = MachineEMSLive.get(machine_id=machine_id)
        if not live_status:
            return {
                "machine_id": machine_id,
                "machine_name": machine_name,
                "status": "OFFLINE",
                "timestamp": datetime.now().isoformat()
            }

        return {
            "machine_id": machine_id,
            "machine_name": machine_name,
            "status": live_status.status,
            "timestamp": live_status.timestamp.isoformat(),
            "phase_a_voltage": live_status.phase_a_voltage,
            "phase_b_voltage": live_status.phase_b_voltage,
            "phase_c_voltage": live_status.phase_c_voltage,
            "avg_phase_voltage": live_status.avg_phase_voltage,
            "line_ab_voltage": live_status.line_ab_voltage,
            "line_bc_voltage": live_status.line_bc_voltage,
            "line_ca_voltage": live_status.line_ca_voltage,
            "avg_line_voltage": live_status.avg_line_voltage,
            "phase_a_current": live_status.phase_a_current,
            "phase_b_current": live_status.phase_b_current,
            "phase_c_current": live_status.phase_c_current,
            "avg_three_phase_current": live_status.avg_three_phase_current,
            "power_factor": live_status.power_factor,
            "frequency": live_status.frequency,
            "total_instantaneous_power": live_status.total_instantaneous_power,
            "active_energy_delivered": live_status.active_energy_delivered
        }


async def monitor_single_machine(machine_id: int, tracker: SingleMachineParameterTracker):
    """Background task to monitor changes for a specific machine"""
    connection_key = (machine_id, 'parameters')
    while True:
        try:
            current_data = await get_single_machine_parameters(machine_id)
            changed_data = tracker.detect_changes(current_data)

            if changed_data:
                await machine_parameter_managers[connection_key].broadcast(f"data: {json.dumps(changed_data)}\n\n")

            await asyncio.sleep(1)
        except Exception as e:
            print(f"Error in single machine monitor task for machine {machine_id}: {str(e)}")
            await asyncio.sleep(1)


async def single_machine_event_generator(request: Request, machine_id: int):
    """Generator for individual machine parameter SSE events"""
    connection_key = (machine_id, 'parameters')
    client_queue = None
    try:
        client_queue = await machine_parameter_managers[connection_key].connect()

        # Send initial state
        initial_data = await get_single_machine_parameters(machine_id)
        yield f"data: {json.dumps(initial_data)}\n\n"

        # Listen for updates
        while True:
            try:
                data = await client_queue.get()
                if data is None:  # Check for shutdown signal
                    break
                yield data
            except asyncio.CancelledError:
                print("Client connection was cancelled")
                break
            except Exception as e:
                print(f"Error in single machine generator for machine {machine_id}: {str(e)}")
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                await asyncio.sleep(1)
    except Exception as e:
        print(f"Fatal error in single machine generator for machine {machine_id}: {str(e)}")
        if client_queue:
            yield f"event: error\ndata: {json.dumps({'error': 'Connection error, please refresh'})}\n\n"
    finally:
        if client_queue:
            await client_queue.put(None)  # Signal shutdown
            machine_parameter_managers[connection_key].disconnect(client_queue)
            print(f"Cleaned up single machine connection for machine {machine_id}")


@router.get("/machine/{machine_id}/parameters-stream")
async def stream_single_machine_parameters(machine_id: int, request: Request):
    """
    Server-Sent Events (SSE) endpoint for real-time parameter updates of a specific machine.
    Supports multiple clients connecting simultaneously.
    """
    # Verify machine exists in history
    with db_session:
        machine_exists = select(h for h in MachineEMSHistory if h.machine_id == machine_id).exists()
        if not machine_exists:
            raise HTTPException(status_code=404, detail=f"Machine {machine_id} not found in history data")

    # Create a dedicated tracker for this machine
    tracker = SingleMachineParameterTracker(machine_id)

    # Start the monitoring task
    task = asyncio.create_task(monitor_single_machine(machine_id, tracker))

    return StreamingResponse(
        single_machine_event_generator(request, machine_id),
        media_type="text/event-stream",
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


class MachineInfo(BaseModel):
    machine_id: int
    machine_name: str


@router.get("/machines", response_model=List[MachineInfo])
async def get_available_machines():
    """Get list of all available machines for monitoring"""
    with db_session:
        machines = select(m for m in Machine)[:]
        return [
            MachineInfo(
                machine_id=m.id,
                machine_name=f"{m.work_center.code}-{m.make}" if hasattr(m, 'work_center') else f"Machine-{m.id}"
            )
            for m in machines
        ]


class ParameterEnum(str, Enum):
    phase_a_voltage = "phase_a_voltage"
    phase_b_voltage = "phase_b_voltage"
    phase_c_voltage = "phase_c_voltage"
    avg_phase_voltage = "avg_phase_voltage"
    line_ab_voltage = "line_ab_voltage"
    line_bc_voltage = "line_bc_voltage"
    line_ca_voltage = "line_ca_voltage"
    avg_line_voltage = "avg_line_voltage"
    phase_a_current = "phase_a_current"
    phase_b_current = "phase_b_current"
    phase_c_current = "phase_c_current"
    avg_three_phase_current = "avg_three_phase_current"
    power_factor = "power_factor"
    frequency = "frequency"
    total_instantaneous_power = "total_instantaneous_power"
    active_energy_delivered = "active_energy_delivered"


class HistoricalDataTracker:
    def __init__(self, machine_id: int, parameter: str):
        self.machine_id = machine_id
        self.parameter = parameter
        self.last_timestamp = None
        self.window_minutes = 30

    async def get_historical_data(self):
        """Get historical data for the last 30 minutes from the latest record"""
        with db_session:
            # First, get the latest timestamp
            latest_record = select(h for h in MachineEMSHistory
                                   if h.machine_id == self.machine_id).order_by(lambda h: desc(h.timestamp)).first()

            if not latest_record:
                # Get machine name even if no history exists
                machine = Machine.get(id=self.machine_id)
                machine_name = machine.make if machine else f"Unknown Machine {self.machine_id}"
                return {
                    "machine_id": self.machine_id,
                    "machine_name": machine_name,
                    "parameter": self.parameter,
                    "data_points": []
                }

            current_time = latest_record.timestamp
            start_time = current_time - timedelta(minutes=self.window_minutes)

            # Query historical data within the window
            query = select(h for h in MachineEMSHistory
                           if h.machine_id == self.machine_id and
                           h.timestamp >= start_time and
                           h.timestamp <= current_time)
            history_data = list(query.order_by(lambda h: h.timestamp)[:])

            # Get machine name from Machine table
            machine = Machine.get(id=self.machine_id)
            machine_name = machine.make if machine else f"Unknown Machine {self.machine_id}"

            # Format response data
            response_data = {
                "machine_id": self.machine_id,
                "machine_name": machine_name,
                "parameter": self.parameter,
                "data_points": [
                    {
                        "timestamp": h.timestamp.isoformat(),
                        "value": getattr(h, self.parameter)
                    }
                    for h in history_data
                    if getattr(h, self.parameter) is not None
                ]
            }

            # Update last timestamp
            self.last_timestamp = current_time

            return response_data

    async def get_new_data(self):
        """Get new data and maintain a 30-minute rolling window"""
        if not self.last_timestamp:
            return await self.get_historical_data()

        with db_session:
            # First check for new data
            latest_record = select(h for h in MachineEMSHistory
                                   if h.machine_id == self.machine_id).order_by(lambda h: desc(h.timestamp)).first()

            if not latest_record or latest_record.timestamp <= self.last_timestamp:
                return None

            # Calculate the new window
            current_time = latest_record.timestamp
            start_time = current_time - timedelta(minutes=self.window_minutes)

            # Query all data within the new window
            query = select(h for h in MachineEMSHistory
                           if h.machine_id == self.machine_id and
                           h.timestamp >= start_time and
                           h.timestamp <= current_time)
            window_data = list(query.order_by(lambda h: h.timestamp)[:])

            if not window_data:
                return None

            # Get machine name from Machine table
            machine = Machine.get(id=self.machine_id)
            machine_name = machine.make if machine else f"Unknown Machine {self.machine_id}"

            # Format response data
            response_data = {
                "machine_id": self.machine_id,
                "machine_name": machine_name,
                "parameter": self.parameter,
                "data_points": [
                    {
                        "timestamp": h.timestamp.isoformat(),
                        "value": getattr(h, self.parameter)
                    }
                    for h in window_data
                    if getattr(h, self.parameter) is not None
                ]
            }

            # Update last timestamp
            self.last_timestamp = current_time

            return response_data


async def monitor_historical_parameter(machine_id: int, parameter: str, tracker: HistoricalDataTracker):
    """Background task to monitor historical parameter changes"""
    connection_key = (machine_id, parameter)
    while True:
        try:
            new_data = await tracker.get_new_data()
            if new_data:
                await history_connection_manager[connection_key].broadcast(f"data: {json.dumps(new_data)}\n\n")
            await asyncio.sleep(1)
        except Exception as e:
            print(f"Error in historical parameter monitor task: {str(e)}")
            await asyncio.sleep(1)


async def historical_parameter_generator(request: Request, machine_id: int, parameter: str):
    """Generator for historical parameter SSE events"""
    connection_key = (machine_id, parameter)
    client_queue = None
    try:
        client_queue = await history_connection_manager[connection_key].connect()
        tracker = HistoricalDataTracker(machine_id, parameter)

        # Send initial historical data
        initial_data = await tracker.get_historical_data()
        yield f"data: {json.dumps(initial_data)}\n\n"

        # Listen for updates
        while True:
            try:
                data = await client_queue.get()
                if data is None:  # Check for shutdown signal
                    break
                yield data
            except asyncio.CancelledError:
                print("Client connection was cancelled")
                break
            except Exception as e:
                print(f"Error in historical parameter generator: {str(e)}")
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                await asyncio.sleep(1)
    except Exception as e:
        print(f"Fatal error in historical parameter generator: {str(e)}")
        if client_queue:
            yield f"event: error\ndata: {json.dumps({'error': 'Connection error, please refresh'})}\n\n"
    finally:
        if client_queue:
            await client_queue.put(None)  # Signal shutdown
            history_connection_manager[connection_key].disconnect(client_queue)
            print(f"Cleaned up historical parameter connection for machine {machine_id}, parameter {parameter}")


@router.get("/machine/{machine_id}/parameter/{parameter}/history-stream")
async def stream_machine_parameter_history(
        machine_id: int,
        parameter: ParameterEnum,
        request: Request
):
    """
    Server-Sent Events (SSE) endpoint for streaming historical parameter data.
    Returns data for the last 30 minutes and streams new data as it arrives.
    """
    # Verify machine exists in history
    with db_session:
        machine_exists = select(h for h in MachineEMSHistory if h.machine_id == machine_id).exists()
        if not machine_exists:
            raise HTTPException(status_code=404, detail=f"Machine {machine_id} not found in history data")

    # Create a dedicated tracker for this machine and parameter
    tracker = HistoricalDataTracker(machine_id, parameter)

    # Start the monitoring task
    task = asyncio.create_task(monitor_historical_parameter(machine_id, parameter, tracker))

    return StreamingResponse(
        historical_parameter_generator(request, machine_id, parameter),
        media_type="text/event-stream",
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


@router.get("/machine/{machine_id}/parameter/{parameter}/history")
async def get_machine_parameter_history(
        machine_id: int,
        parameter: ParameterEnum,
        start_time: int = Query(..., description="Start time in epoch (seconds)"),
        end_time: int = Query(..., description="End time in epoch (seconds)")
):
    """
    Get historical data for a specific machine parameter within a time range.

    Args:
        machine_id: ID of the machine
        parameter: Parameter to fetch (e.g., phase_a_voltage)
        start_time: Start time in epoch seconds (without GMT)
        end_time: End time in epoch seconds (without GMT)

    Returns:
        List of data points with timestamp and value
    """
    try:
        # Convert epoch to datetime
        start_datetime = datetime.fromtimestamp(start_time)
        end_datetime = datetime.fromtimestamp(end_time)

        # Validate time range
        if end_datetime <= start_datetime:
            raise HTTPException(
                status_code=400,
                detail="End time must be greater than start time"
            )

        # Maximum time range of 7 days
        max_duration = timedelta(days=7)
        if end_datetime - start_datetime > max_duration:
            raise HTTPException(
                status_code=400,
                detail="Time range cannot exceed 7 days"
            )

        with db_session:
            # Verify machine exists in history
            machine_exists = select(h for h in MachineEMSHistory if h.machine_id == machine_id).exists()
            if not machine_exists:
                raise HTTPException(
                    status_code=404,
                    detail=f"Machine {machine_id} not found in history data"
                )

            # Query data within time range
            query = select(h for h in MachineEMSHistory
                           if h.machine_id == machine_id and
                           h.timestamp >= start_datetime and
                           h.timestamp <= end_datetime)

            # Order by timestamp
            history_data = list(query.order_by(lambda h: h.timestamp)[:])

            # Format response data
            response_data = {
                "machine_id": machine_id,
                "parameter": parameter,
                "start_time": start_datetime.isoformat(),
                "end_time": end_datetime.isoformat(),
                "data_points": [
                    {
                        "timestamp": h.timestamp.isoformat(),
                        "epoch": int(h.timestamp.timestamp()),  # Include epoch for convenience
                        "value": getattr(h, parameter)
                    }
                    for h in history_data
                    if getattr(h, parameter) is not None  # Filter out None values
                ]
            }

            # Add some statistics
            values = [point["value"] for point in response_data["data_points"]]
            if values:
                response_data["statistics"] = {
                    "count": len(values),
                    "min": min(values),
                    "max": max(values),
                    "average": sum(values) / len(values)
                }
            else:
                response_data["statistics"] = {
                    "count": 0,
                    "min": None,
                    "max": None,
                    "average": None
                }

            return JSONResponse(content=response_data)

    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid timestamp format: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


class ShiftwiseEnergyTracker:
    def __init__(self):
        self.previous_states = {}
        self.last_broadcast_time = datetime.min
        self.MIN_BROADCAST_INTERVAL = 5.0  # Minimum seconds between broadcasts
        self.NUMERIC_THRESHOLD = 0.01  # Threshold for detecting significant changes

    def _is_significant_change(self, curr_value, prev_value):
        """Helper method to determine if a change is significant enough to broadcast"""
        if curr_value is None or prev_value is None:
            return curr_value != prev_value

        # Handle numeric comparisons with threshold
        if isinstance(curr_value, (int, float)) and isinstance(prev_value, (int, float)):
            return abs(curr_value - prev_value) > self.NUMERIC_THRESHOLD

        return curr_value != prev_value

    def detect_changes(self, current_data):
        """Detects if there are any changes in the shiftwise energy data"""
        if not current_data:
            return None

        current_time = datetime.now()

        # Check if enough time has passed since last broadcast
        time_since_last = (current_time - self.last_broadcast_time).total_seconds()
        if time_since_last < self.MIN_BROADCAST_INTERVAL:
            return None

        changed_machines = []
        has_significant_changes = False

        # Create a map of current machine states
        current_states = {str(data['machine_id']): data for data in current_data}

        # Check for changes in existing machines
        for machine_id, current_state in current_states.items():
            previous_state = self.previous_states.get(machine_id)

            # If no previous state, consider it as changed
            if not previous_state:
                changed_machines.append(current_state)
                has_significant_changes = True
                continue

            # Check each energy value for significant changes
            has_changes = False
            for key in ['first_shift', 'second_shift', 'third_shift', 'total_energy']:
                if self._is_significant_change(
                        current_state.get(key),
                        previous_state.get(key)
                ):
                    has_changes = True
                    has_significant_changes = True
                    break

            if has_changes:
                changed_machines.append(current_state)

        # Only update previous states and broadcast if there were significant changes
        if has_significant_changes:
            self.previous_states = current_states
            self.last_broadcast_time = current_time
            return changed_machines

        return None


async def get_all_shiftwise_energy():
    """Helper function to get shiftwise energy data for all machines"""
    with db_session:
        live_data = select(s for s in ShiftwiseEnergyLive)[:]

        # Get all machines for name lookup
        machines = {m.id: m.make for m in select(m for m in Machine)[:]}

        return [
            {
                "machine_id": data.machine_id,
                "machine_name": machines.get(data.machine_id, f"Unknown Machine {data.machine_id}"),
                "timestamp": data.timestamp.isoformat(),
                "first_shift": data.first_shift,
                "second_shift": data.second_shift,
                "third_shift": data.third_shift,
                "total_energy": data.total_energy
            }
            for data in live_data
        ]


async def monitor_shiftwise_energy(tracker: ShiftwiseEnergyTracker):
    """Background task to monitor shiftwise energy changes"""
    while True:
        try:
            current_data = await get_all_shiftwise_energy()
            changed_data = tracker.detect_changes(current_data)

            if changed_data:
                await shiftwise_energy_manager.broadcast(f"data: {json.dumps(changed_data)}\n\n")

            await asyncio.sleep(1)  # Check every second but broadcast based on tracker settings
        except Exception as e:
            print(f"Error in shiftwise energy monitor task: {str(e)}")
            await asyncio.sleep(1)


async def shiftwise_energy_generator(request: Request):
    """Generator for shiftwise energy SSE events"""
    client_queue = None
    try:
        client_queue = await shiftwise_energy_manager.connect()

        # Send initial state
        initial_data = await get_all_shiftwise_energy()
        yield f"data: {json.dumps(initial_data)}\n\n"

        # Listen for updates
        while True:
            try:
                data = await client_queue.get()
                if data is None:  # Check for shutdown signal
                    break
                yield data
            except asyncio.CancelledError:
                print("Client connection was cancelled")
                break
            except Exception as e:
                print(f"Error in shiftwise energy generator: {str(e)}")
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                await asyncio.sleep(1)
    except Exception as e:
        print(f"Fatal error in shiftwise energy generator: {str(e)}")
        if client_queue:
            yield f"event: error\ndata: {json.dumps({'error': 'Connection error, please refresh'})}\n\n"
    finally:
        if client_queue:
            await client_queue.put(None)  # Signal shutdown
            shiftwise_energy_manager.disconnect(client_queue)
            print("Cleaned up shiftwise energy connection")


@router.get("/shiftwise-energy-stream")
async def stream_shiftwise_energy(request: Request):
    """
    Server-Sent Events (SSE) endpoint for real-time shiftwise energy updates.
    Returns data for all machines and streams new data as it arrives.
    """
    # Create a dedicated tracker
    tracker = ShiftwiseEnergyTracker()

    # Start the monitoring task
    task = asyncio.create_task(monitor_shiftwise_energy(tracker))

    return StreamingResponse(
        shiftwise_energy_generator(request),
        media_type="text/event-stream",
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


@router.get("/combined-history/")
async def get_combined_history_range(
    from_timestamp: int = Query(..., description="Start timestamp in epoch seconds"),
    to_timestamp: int = Query(..., description="End timestamp in epoch seconds")
):
    """
    Get combined shiftwise energy data between two timestamps.
    Aggregates data for all machines and returns cumulative totals.
    """
    try:
        # Convert epoch to datetime
        from_dt = datetime.fromtimestamp(from_timestamp)
        to_dt = datetime.fromtimestamp(to_timestamp)

        if from_dt > to_dt:
            raise HTTPException(status_code=400, detail="from_timestamp must be earlier than to_timestamp")

        with db_session:
            # Get all machines with data in the time range
            local_context = locals()
            local_context["ShiftwiseEnergyHistory"] = ShiftwiseEnergyHistory

            shiftwise_data = select(
                "h for h in ShiftwiseEnergyHistory if from_dt <= h.timestamp <= to_dt",
                local_context
            )[:]


            # Group by machine_id
            machine_aggregates = {}
            grand_totals = {"first_shift": 0, "second_shift": 0, "third_shift": 0, "total_energy": 0}

            # Get machine names
            machines = {m.id: m.make for m in select(m for m in Machine)[:]}

            for h in shiftwise_data:
                m_id = h.machine_id
                if m_id not in machine_aggregates:
                    machine_aggregates[m_id] = {
                        "machine_id": m_id,
                        "machine_name": machines.get(m_id, f"Unknown Machine {m_id}"),
                        "first_shift": 0,
                        "second_shift": 0,
                        "third_shift": 0,
                        "total_energy": 0
                    }

                machine_aggregates[m_id]["first_shift"] += h.first_shift or 0
                machine_aggregates[m_id]["second_shift"] += h.second_shift or 0
                machine_aggregates[m_id]["third_shift"] += h.third_shift or 0
                machine_aggregates[m_id]["total_energy"] += h.total_energy or 0

                # Add to grand totals
                grand_totals["first_shift"] += h.first_shift or 0
                grand_totals["second_shift"] += h.second_shift or 0
                grand_totals["third_shift"] += h.third_shift or 0
                grand_totals["total_energy"] += h.total_energy or 0

            # Round totals
            for m in machine_aggregates.values():
                for key in ["first_shift", "second_shift", "third_shift", "total_energy"]:
                    m[key] = round(m[key], 2)

            for key in grand_totals:
                grand_totals[key] = round(grand_totals[key], 2)

            response_data = {
                "from_timestamp": from_dt.isoformat(),
                "to_timestamp": to_dt.isoformat(),
                "epoch_range": {
                    "from": from_timestamp,
                    "to": to_timestamp
                },
                "grand_totals": {
                    "total_first_shift": grand_totals["first_shift"],
                    "total_second_shift": grand_totals["second_shift"],
                    "total_third_shift": grand_totals["third_shift"],
                    "grand_total_energy": grand_totals["total_energy"]
                },
                "machines": list(machine_aggregates.values()),
                "machine_totals": grand_totals
            }

            return JSONResponse(content=response_data)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid timestamp format: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
