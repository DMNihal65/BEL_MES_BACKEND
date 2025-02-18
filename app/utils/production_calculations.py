from datetime import datetime, timedelta
import random
from typing import List, Dict
from pony.orm import select

def calculate_machine_uptime(machine_id: int) -> float:
    """Simulate machine uptime in hours"""
    return random.uniform(6.0, 23.0)

def calculate_machine_efficiency(machine_id: int) -> float:
    """Simulate machine efficiency percentage"""
    return random.uniform(75.0, 98.0)

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