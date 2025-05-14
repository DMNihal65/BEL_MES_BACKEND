from datetime import datetime
from typing import Any, Dict

from pony.orm import Required, Optional, PrimaryKey
from ..database.connection import db

import logging
from typing import Dict, Set, Literal
from fastapi import WebSocket, WebSocketDisconnect
logger = logging.getLogger("uvicorn.error")


class MachineEMSHistory(db.Entity):
    _table_ = ('ems', 'machine_ems_history')

    machine_id = Required(int)
    timestamp = Required(datetime, default=datetime.now)
    phase_a_voltage = Optional(float)
    phase_b_voltage = Optional(float)
    phase_c_voltage = Optional(float)
    avg_phase_voltage = Optional(float)
    line_ab_voltage = Optional(float)
    line_bc_voltage = Optional(float)
    line_ca_voltage = Optional(float)
    avg_line_voltage = Optional(float)
    phase_a_current = Optional(float)
    phase_b_current = Optional(float)
    phase_c_current = Optional(float)
    avg_three_phase_current = Optional(float)
    power_factor = Optional(float)
    frequency = Optional(float)
    total_instantaneous_power = Optional(float)
    active_energy_delivered = Optional(float)

    # Using SQL constraints directly in _sql_constraints class attribute
    _sql_constraints_ = [
        'FOREIGN KEY (machine_id) REFERENCES master_order.machines (id)'
    ]


class MachineEMSLive(db.Entity):
    _table_ = ('ems', 'machine_ems_live')

    machine_id = Required(int, unique=True)
    timestamp = Required(datetime, default=datetime.now)
    phase_a_voltage = Optional(float)
    phase_b_voltage = Optional(float)
    phase_c_voltage = Optional(float)
    avg_phase_voltage = Optional(float)
    line_ab_voltage = Optional(float)
    line_bc_voltage = Optional(float)
    line_ca_voltage = Optional(float)
    avg_line_voltage = Optional(float)
    phase_a_current = Optional(float)
    phase_b_current = Optional(float)
    phase_c_current = Optional(float)
    avg_three_phase_current = Optional(float)
    power_factor = Optional(float)
    frequency = Optional(float)
    total_instantaneous_power = Optional(float)
    active_energy_delivered = Optional(float)
    status = Optional(int)

    _sql_constraints_ = [
        'FOREIGN KEY (machine_id) REFERENCES master_order.machines (id)'
    ]


class ShiftwiseEnergyLive(db.Entity):
    _table_ = ('ems', 'shiftwise_energy_live')

    timestamp = Required(datetime, default=datetime.now)
    first_shift = Required(float)
    second_shift = Required(float)
    third_shift = Required(float)
    total_energy = Required(float)
    machine_id = Required(int)

    _sql_constraints_ = [
        'FOREIGN KEY (machine_id) REFERENCES master_order.machines (id)'
    ]


class ShiftwiseEnergyHistory(db.Entity):
    _table_ = ('ems', 'shiftwise_energy_history')

    timestamp = Required(datetime, default=datetime.now)
    first_shift = Required(float)
    second_shift = Required(float)
    third_shift = Required(float)
    total_energy = Required(float)
    machine_id = Required(int)

    _sql_constraints_ = [
        'FOREIGN KEY (machine_id) REFERENCES master_order.machines (id)'
    ]





class WebSocketManager:
    def __init__(self):
        # Structure: { machine_id: { "raw": set(), "live": set() } }
        self.active_connections: Dict[int, Dict[str, Set[WebSocket]]] = {}

    async def connect(self, websocket: WebSocket, machine_id: int, data_type: Literal["raw", "live"]):
        await websocket.accept()
        if machine_id not in self.active_connections:
            self.active_connections[machine_id] = {"raw": set(), "live": set()}
        self.active_connections[machine_id][data_type].add(websocket)
        logger.info(f"WebSocket connected: machine_id={machine_id}, data_type={data_type}")

    def disconnect(self, websocket: WebSocket, machine_id: int, data_type: Literal["raw", "live"]):
        if machine_id in self.active_connections:
            self.active_connections[machine_id][data_type].discard(websocket)
            logger.info(f"WebSocket disconnected: machine_id={machine_id}, data_type={data_type}")

            # Cleanup if no more connections
            if not self.active_connections[machine_id]["raw"] and not self.active_connections[machine_id]["live"]:
                del self.active_connections[machine_id]

    async def broadcast_to_machine(self, machine_id: int, data: dict, data_type: Literal["raw", "live"]):
        if machine_id in self.active_connections:
            websockets = self.active_connections[machine_id].get(data_type, set())
            disconnected = []
            for websocket in websockets:
                try:
                    await websocket.send_json(data)
                except WebSocketDisconnect:
                    disconnected.append(websocket)
                except Exception as e:
                    logger.error(f"Failed to send message to WebSocket: {e}")
                    disconnected.append(websocket)

            # Remove disconnected websockets
            for ws in disconnected:
                self.disconnect(ws, machine_id, data_type)

