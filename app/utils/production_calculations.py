from datetime import datetime, timedelta
import random
from typing import List, Dict
from pony.orm import db_session, select, avg, count, desc, sum
from app.models.production import MachineRaw, MachineRawLive, StatusLookup
from app.models import ProductionLog, Machine
from app.database.connection import db

@db_session
def calculate_machine_uptime(machine_id: int, start_time: datetime, end_time: datetime) -> float:
    """Calculate actual machine uptime based on status data"""
    total_time = (end_time - start_time).total_seconds() / 3600  # hours
    production_records = count(s for s in MachineRaw 
        if s.machine_id == machine_id 
        and s.time_stamp >= start_time 
        and s.time_stamp <= end_time 
        and s.status.status_name in ['ON', 'PRODUCTION']
    )
    total_records = count(s for s in MachineRaw 
        if s.machine_id == machine_id 
        and s.time_stamp >= start_time 
        and s.time_stamp <= end_time
    )
    return (production_records / total_records * 100) if total_records > 0 else 0

@db_session
def get_machine_current_status(machine_id: int) -> dict:
    """Get current machine status from MachineRawLive"""
    live_status = MachineRawLive.get(machine_id=machine_id)
    if live_status:
        return {
            'status': live_status.status.status_name,
            'program': live_status.active_program,
            'part_count': live_status.part_count or 0,
            'job_status': live_status.job_status,
            'timestamp': live_status.time_stamp
        }
    return None

@db_session
def calculate_machine_efficiency(machine_id: int, start_time: datetime, end_time: datetime) -> float:
    """Calculate machine efficiency based on production status"""
    total_records = count(s for s in MachineRaw 
                        if s.machine_id == machine_id 
                        and s.time_stamp >= start_time 
                        and s.time_stamp <= end_time)
    
    production_records = count(s for s in MachineRaw 
                             if s.machine_id == machine_id 
                             and s.time_stamp >= start_time 
                             and s.time_stamp <= end_time 
                             and s.status.status_name == 'PRODUCTION')
    
    return (production_records / total_records * 100) if total_records > 0 else 0

@db_session
def get_machine_production_metrics(machine_id: int, start_time: datetime, end_time: datetime) -> dict:
    """Get detailed production metrics for a machine"""
    records = select(r for r in MachineRaw 
                    if r.machine_id == machine_id 
                    and r.time_stamp >= start_time 
                    and r.time_stamp <= end_time)
    
    status_counts = {}
    total_records = 0
    
    for status in StatusLookup.select():
        status_count = count(r for r in records if r.status == status)
        status_counts[status.status_name] = status_count
        total_records += status_count
    
    part_counts = select(r.part_count for r in records if r.part_count is not None).fetch()
    max_part_count = max(part_counts) if part_counts else 0
    
    return {
        'status_distribution': {
            status: (count / total_records * 100) if total_records > 0 else 0 
            for status, count in status_counts.items()
        },
        'part_count': max_part_count,
        'programs_used': list(set(r.active_program for r in records if r.active_program))
    }

@db_session
def calculate_shift_metrics(shift_start: datetime, shift_end: datetime) -> dict:
    """Calculate production metrics for a shift"""
    machines = select(m for m in Machine)
    shift_metrics = {}
    
    for machine in machines:
        records = select(r for r in MachineRaw 
                       if r.machine_id == machine.id 
                       and r.time_stamp >= shift_start 
                       and r.time_stamp <= shift_end)
        
        production_time = count(r for r in records if r.status.status_name == 'PRODUCTION')
        total_time = count(r for r in records)
        
        shift_metrics[machine.id] = {
            'efficiency': (production_time / total_time * 100) if total_time > 0 else 0,
            'part_count': max((r.part_count or 0) for r in records) if records else 0,
            'status_changes': count(records)
        }
        
    return shift_metrics

@db_session
def get_production_trends(start_time: datetime, end_time: datetime, interval_minutes: int = 60) -> List[dict]:
    """Get production trends over time"""
    trends = []
    current_time = start_time
    
    while current_time <= end_time:
        interval_end = current_time + timedelta(minutes=interval_minutes)
        
        machines_data = {}
        for machine in Machine.select():
            records = select(r for r in MachineRaw 
                           if r.machine_id == machine.id 
                           and r.time_stamp >= current_time 
                           and r.time_stamp < interval_end)
            
            production_time = count(r for r in records if r.status.status_name == 'PRODUCTION')
            total_time = count(r for r in records)
            
            machines_data[machine.id] = {
                'efficiency': (production_time / total_time * 100) if total_time > 0 else 0,
                'part_count': max((r.part_count or 0) for r in records) if records else 0
            }
        
        trends.append({
            'timestamp': current_time,
            'machines': machines_data
        })
        
        current_time = interval_end
        
    return trends

def calculate_overall_machine_utilization() -> float:
    """Simulate overall machine utilization percentage"""
    return random.uniform(65.0, 95.0)

def calculate_cycle_time_variance() -> float:
    """Simulate cycle time variance in minutes"""
    return random.uniform(0.5, 5.0)

def calculate_average_setup_time() -> float:
    """Simulate average setup time in minutes"""
    return random.uniform(15.0, 45.0)

def calculate_total_downtime() -> float:
    """Simulate total downtime in minutes"""
    return random.uniform(30.0, 180.0)

def calculate_shift_downtime(shift_start: datetime, shift_end: datetime) -> float:
    """Simulate shift downtime in minutes"""
    shift_duration = (shift_end - shift_start).total_seconds() / 3600  # hours
    return random.uniform(0.0, shift_duration * 0.2 * 60)  # max 20% of shift duration in minutes

def calculate_shift_efficiency(shift_start: datetime, shift_end: datetime) -> float:
    """Simulate shift efficiency percentage"""
    return random.uniform(70.0, 95.0)

def calculate_rework_rate() -> float:
    """Simulate rework rate percentage"""
    return random.uniform(1.0, 5.0)

def calculate_scrap_rate() -> float:
    """Simulate scrap rate percentage"""
    return random.uniform(0.5, 3.0)

def calculate_first_pass_yield() -> float:
    """Simulate first pass yield percentage"""
    return random.uniform(90.0, 98.0)

def analyze_defect_categories() -> Dict[str, int]:
    """Simulate defect categories and their counts"""
    categories = {
        "Dimensional": random.randint(5, 20),
        "Surface Finish": random.randint(3, 15),
        "Material Defect": random.randint(1, 10),
        "Tool Mark": random.randint(2, 12),
        "Setup Error": random.randint(1, 8)
    }
    return categories

def get_recent_quality_issues() -> List[Dict[str, str]]:
    """Simulate recent quality issues"""
    issues = [
        {"issue": "Dimensional out of tolerance", "severity": "High", "status": "Open"},
        {"issue": "Surface finish not meeting specs", "severity": "Medium", "status": "In Progress"},
        {"issue": "Tool marks on critical surface", "severity": "Low", "status": "Resolved"},
        {"issue": "Material hardness variation", "severity": "Medium", "status": "Open"}
    ]
    return random.sample(issues, random.randint(2, 4))

def calculate_machine_utilization_rate(machine_id: int) -> float:
    """Simulate machine utilization rate percentage"""
    return random.uniform(60.0, 90.0)

def calculate_productive_time(logs) -> float:
    """Simulate productive time in hours"""
    return random.uniform(6.0, 7.5)

def calculate_idle_time(logs) -> float:
    """Simulate idle time in hours"""
    return random.uniform(0.2, 1.5)

def calculate_machine_setup_time(logs) -> float:
    """Simulate machine setup time in hours"""
    return random.uniform(0.25, 0.75)

def calculate_breakdown_time(logs) -> float:
    """Simulate breakdown time in hours"""
    return random.uniform(0.0, 0.5)

def calculate_maintenance_time(logs) -> float:
    """Simulate maintenance time in hours"""
    return random.uniform(0.25, 1.0)

def calculate_production_rate(logs) -> float:
    """Simulate production rate (pieces per hour)"""
    return random.uniform(50, 150)

def calculate_quality_rate(logs) -> float:
    """Simulate quality rate percentage"""
    return random.uniform(90, 99)

def calculate_utilization_rate(logs) -> float:
    """Simulate utilization rate percentage"""
    return random.uniform(65, 95) 