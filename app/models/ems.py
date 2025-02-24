from datetime import datetime
from pony.orm import Database, Required, Optional, Set
from ..database.connection import db


class EMSMachine(db.Entity):
    _table_ = ('EMS', 'machine')

    machine_name = Required(str)
    workshop_name = Required(str)
    mqtt_topic = Required(str)
    mqtt_mach_iden = Required(str)
    histories = Set('MachineHistory')
    recents = Set('Recent')
    on_states = Set('On')
    off_states = Set('Off')
    prod_states = Set('Production')
    graph_data = Set('Graph')
    state = Set('State')
    shiftlive = Set('ShiftwiseEnergyLive')
    shifthistory = Set('ShiftwiseEnergyHistory')


class MachineHistory(db.Entity):
    _table_ = ('EMS', 'machine_history')

    current = Optional(float)
    power = Optional(float)
    energy = Optional(float)
    timestamp = Required(datetime, default=datetime.now)
    machine_id = Required(EMSMachine)


class Recent(db.Entity):
    _table_ = ('EMS', 'machine_recent')

    current = Optional(float)
    power = Optional(float)
    energy = Optional(float)
    timestamp = Required(datetime, default=datetime.now)
    machine_id = Required(EMSMachine, unique=True)


class On(db.Entity):
    _table_ = ('EMS', 'on_state')

    current = Required(str)
    timestamp = Required(datetime)
    machine_id = Required(EMSMachine)


class Off(db.Entity):
    _table_ = ('EMS', 'off_state')

    current = Required(str)
    timestamp = Required(datetime)
    machine_id = Required(EMSMachine)


class Production(db.Entity):
    _table_ = ('EMS', 'production_state')

    current = Required(str)
    timestamp = Required(datetime)
    machine_id = Required(EMSMachine)


class State(db.Entity):
    _table_ = ('EMS', 'check_state')

    start_current_range = Required(float)
    end_current_range = Required(float)
    state = Required(str)
    machine_id = Required(EMSMachine)


class Graph(db.Entity):
    _table_ = ('EMS', 'graph_data')

    start_time = Required(datetime)
    end_time = Required(datetime)
    state = Required(str)
    machine_id = Required(EMSMachine)


class ShiftwiseEnergyLive(db.Entity):
    _table_ = ('EMS', 'shiftwise_energy_live')

    timestamp = Required(datetime, default=datetime.now)
    first_shift = Required(float)
    second_shift = Required(float)
    third_shift = Required(float)
    total_energy = Required(float)
    machine_id = Required(EMSMachine)


class ShiftwiseEnergyHistory(db.Entity):
    _table_ = ('EMS', 'shiftwise_energy_history')

    timestamp = Required(datetime, default=datetime.now)
    first_shift = Required(float)
    second_shift = Required(float)
    third_shift = Required(float)
    total_energy = Required(float)
    machine_id = Required(EMSMachine)
