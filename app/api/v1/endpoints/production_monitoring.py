from app.schemas.scheduled import ScheduledOperation, ProductionLogResponse, CombinedScheduleProductionResponse

from fastapi import APIRouter, HTTPException, Query
from pony.orm import db_session, select, avg, count, desc
from app.schemas.scheduled import ScheduledOperation, ScheduleResponse, ProductionMetrics, MachineStatus, ProductionKPI, ShiftSummary, ProductionTrend, QualityMetrics, ResourceUtilization
from app.models import Order, Operation, Machine, PartScheduleStatus, PlannedScheduleItem, ScheduleVersion
from app.crud.operation import fetch_operations
from app.crud.component_quantities import fetch_component_quantities
from app.crud.leadtime import fetch_lead_times
from app.algorithm.scheduling import schedule_operations
import re
from app.models import ProductionLog
from datetime import datetime, timedelta
from typing import List
from app.utils.production_calculations import (
    calculate_machine_uptime, calculate_machine_efficiency,
    calculate_overall_machine_utilization, calculate_cycle_time_variance,
    calculate_average_setup_time, calculate_total_downtime,
    calculate_shift_downtime, calculate_shift_efficiency,
    calculate_rework_rate, calculate_scrap_rate,
    calculate_first_pass_yield, analyze_defect_categories,
    get_recent_quality_issues, calculate_machine_utilization_rate,
    calculate_productive_time, calculate_idle_time,
    calculate_machine_setup_time, calculate_breakdown_time,
    calculate_maintenance_time, calculate_production_rate,
    calculate_quality_rate, calculate_utilization_rate
)

router = APIRouter(prefix="/production_monitoring", tags=["production_monitoring"])




def extract_quantity(quantity_str: str) -> tuple[int, int, int]:
    """
    Extract quantities from process strings like:
    "Process(85/291pcs)" or "Process(85/291pcs, Today: 85pcs)"

    Returns:
        tuple: (total_quantity, current_quantity, today_quantity)
    """
    try:
        if "Process" in quantity_str:
            # Try to match the format with "Today" first
            match = re.search(r'Process\((\d+)/(\d+)pcs, Today: (\d+)pcs\)', quantity_str)
            if match:
                current_qty = int(match.group(1))
                total_qty = int(match.group(2))
                today_qty = int(match.group(3))
                return total_qty, current_qty, today_qty

            # Match the simple format "Process(85/291pcs)"
            match = re.search(r'Process\((\d+)/(\d+)pcs\)', quantity_str)
            if match:
                current_qty = int(match.group(1))
                total_qty = int(match.group(2))
                return total_qty, current_qty, current_qty

        elif "Setup" in quantity_str:
            return 1, 1, 1

        numbers = re.findall(r'\d+', quantity_str)
        if numbers:
            first_num = int(numbers[0])
            return first_num, first_num, first_num

        return 1, 1, 1

    except Exception as e:
        print(f"Error parsing quantity string: {quantity_str}, Error: {str(e)}")
        return 1, 1, 1



@router.get("/actual-planned-schedule/", response_model=CombinedScheduleProductionResponse)
async def get_combined_schedule_production():
    """Retrieve combined production logs with schedule batch information"""
    try:
        with db_session:
            # Get production logs
            logs_query = select((
                                    log,
                                    log.operator,
                                    log.schedule_version,
                                    log.schedule_version.schedule_item,
                                    log.schedule_version.schedule_item.machine,
                                    log.schedule_version.schedule_item.operation,
                                    log.schedule_version.schedule_item.order
                                ) for log in ProductionLog)

            # Dictionary to store combined logs
            combined_logs = {}

            for (log, operator, version, schedule_item, machine, operation, order) in logs_query:
                # Skip logs with null end_time
                if log.end_time is None:
                    continue

                group_key = (
                    order.part_number if order else None,
                    operation.operation_description if operation else None,
                    machine.work_center.code + "-" + machine.make if machine and hasattr(machine,
                                                                                         'work_center') else None,
                    version.version_number if version else None
                )

                is_setup = log.quantity_completed == 1
                machine_name = f"{machine.work_center.code}-{machine.make}" if machine and hasattr(machine,
                                                                                                   'work_center') else None

                if group_key not in combined_logs:
                    combined_logs[group_key] = {
                        'setup': None,
                        'operation': None
                    }

                if is_setup:
                    combined_logs[group_key]['setup'] = {
                        'id': log.id,
                        'start_time': log.start_time,
                        'notes': log.notes
                    }
                else:
                    combined_logs[group_key]['operation'] = {
                        'id': log.id,
                        'end_time': log.end_time,
                        'quantity_completed': log.quantity_completed,
                        'quantity_rejected': log.quantity_rejected,
                        'operator_id': operator.id,
                        'part_number': order.part_number if order else None,
                        'operation_description': operation.operation_description if operation else None,
                        'machine_name': machine_name,
                        'version_number': version.version_number if version else None,
                        'notes': log.notes
                    }

            # Process production logs
            logs_data = []
            total_completed = 0
            total_rejected = 0

            for group_data in combined_logs.values():
                setup = group_data['setup']
                operation = group_data['operation']

                if setup and operation:
                    combined_entry = ProductionLogResponse(
                        id=operation['id'],
                        operator_id=operation['operator_id'],
                        start_time=setup['start_time'],
                        end_time=operation['end_time'],
                        quantity_completed=operation['quantity_completed'],
                        quantity_rejected=operation['quantity_rejected'],
                        part_number=operation['part_number'],
                        operation_description=operation['operation_description'],
                        machine_name=operation['machine_name'],
                        notes=f"Setup: {setup['notes']} | Operation: {operation['notes']}",
                        version_number=operation['version_number']
                    )
                    logs_data.append(combined_entry)
                    total_completed += operation['quantity_completed']
                    total_rejected += operation['quantity_rejected']

            # Get schedule data
            df = fetch_operations()
            component_quantities = fetch_component_quantities()
            lead_times = fetch_lead_times()

            schedule_df, overall_end_time, overall_time, daily_production, _, _ = schedule_operations(
                df, component_quantities, lead_times
            )

            # Dictionary to store combined schedule operations
            combined_schedule = {}

            if not schedule_df.empty:
                machine_details = {
                    machine.id: f"{machine.work_center.code}-{machine.make}"
                    for machine in Machine.select()
                }

                orders_map = {
                    order.part_number: order.production_order
                    for order in Order.select()
                }

                for _, row in schedule_df.iterrows():
                    total_qty, current_qty, today_qty = extract_quantity(row['quantity'])

                    # Create key for grouping schedule operations
                    schedule_key = (
                        row['partno'],
                        row['operation'],
                        machine_details.get(row['machine_id'], f"Machine-{row['machine_id']}"),
                        orders_map.get(row['partno'], '')
                    )

                    is_setup = total_qty == 1

                    if is_setup:
                        if schedule_key not in combined_schedule:
                            combined_schedule[schedule_key] = {
                                'setup_start': row['start_time'],
                                'setup_end': row['end_time'],
                                'operation_end': None,
                                'total_qty': 0,
                                'current_qty': 0,
                                'today_qty': 0
                            }
                    else:
                        if schedule_key in combined_schedule:
                            combined_schedule[schedule_key]['operation_end'] = row['end_time']
                            combined_schedule[schedule_key]['total_qty'] = max(
                                combined_schedule[schedule_key]['total_qty'], total_qty)
                            combined_schedule[schedule_key]['current_qty'] = max(
                                combined_schedule[schedule_key]['current_qty'], current_qty)
                            combined_schedule[schedule_key]['today_qty'] = max(
                                combined_schedule[schedule_key]['today_qty'], today_qty)
                        else:
                            combined_schedule[schedule_key] = {
                                'setup_start': row['start_time'],
                                'setup_end': row['end_time'],
                                'operation_end': row['end_time'],
                                'total_qty': total_qty,
                                'current_qty': current_qty,
                                'today_qty': today_qty
                            }

            scheduled_operations = []

            for (component, description, machine, production_order), data in combined_schedule.items():
                if data['operation_end']:  # Only include completed operations
                    quantity_str = f"Process({data['current_qty']}/{data['total_qty']}pcs, Today: {data['today_qty']}pcs)"
                    scheduled_operations.append(
                        ScheduledOperation(
                            component=component,
                            description=description,
                            machine=machine,
                            start_time=data['setup_start'],
                            end_time=data['operation_end'],
                            quantity=quantity_str,
                            production_order=production_order
                        )
                    )

            return CombinedScheduleProductionResponse(
                production_logs=logs_data,
                total_completed=total_completed,
                total_rejected=total_rejected,
                total_logs=len(logs_data),
                scheduled_operations=scheduled_operations,
                overall_end_time=overall_end_time,
                overall_time=str(overall_time),
                daily_production=daily_production
            )

    except Exception as e:
        print(f"Error in combined schedule production endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/real-time-machine-status/", response_model=List[MachineStatus])
async def get_real_time_machine_status():
    """Get real-time status of all machines on the shop floor"""
    try:
        with db_session:
            current_time = datetime.utcnow()
            machines = select(m for m in Machine)
            
            machine_statuses = []
            for machine in machines:
                # Get the latest production log for this machine
                latest_log = select(
                    log for log in ProductionLog 
                    if log.schedule_version.schedule_item.machine == machine
                ).order_by(lambda l: desc(l.start_time)).first()
                
                # Calculate machine status
                status = "idle"
                current_order = None
                current_operation = None
                start_time = None
                
                if latest_log and latest_log.end_time is None:
                    status = "running"
                    current_order = latest_log.schedule_version.schedule_item.order.production_order
                    current_operation = latest_log.schedule_version.schedule_item.operation.operation_description
                    start_time = latest_log.start_time
                
                machine_statuses.append(MachineStatus(
                    machine_id=machine.id,
                    machine_name=f"{machine.work_center.code}-{machine.make}",
                    status=status,
                    current_order=current_order,
                    current_operation=current_operation,
                    start_time=start_time,
                    uptime=calculate_machine_uptime(machine.id),
                    efficiency=calculate_machine_efficiency(machine.id)
                ))
                
            return machine_statuses
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/production-kpis/", response_model=ProductionKPI)
async def get_production_kpis(
    start_date: datetime = Query(default=None),
    end_date: datetime = Query(default=None)
):
    """Get key performance indicators for production"""
    try:
        with db_session:
            if not start_date:
                start_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            if not end_date:
                end_date = datetime.utcnow()

            # Calculate KPIs
            logs = select(l for l in ProductionLog if l.start_time >= start_date and l.end_time <= end_date)
            
            total_production = sum(l.quantity_completed + l.quantity_rejected for l in logs)
            good_production = sum(l.quantity_completed for l in logs)
            
            # Calculate target production from scheduled items
            target_production = select(
                sum(s.planned_quantity) for s in ScheduleVersion 
                if s.planned_start_time >= start_date and s.planned_end_time <= end_date
            ).first() or 0
            
            return ProductionKPI(
                target_production=target_production,
                actual_production=total_production,
                efficiency=(total_production / target_production * 100) if target_production > 0 else 0,
                quality_rate=(good_production / total_production * 100) if total_production > 0 else 0,
                machine_utilization=calculate_overall_machine_utilization(),
                cycle_time_variance=calculate_cycle_time_variance(),
                setup_time=calculate_average_setup_time(),
                downtime=calculate_total_downtime()
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/shift-summary/", response_model=List[ShiftSummary])
async def get_shift_summary(date: datetime = Query(default=None)):
    """Get production summary for each shift"""
    try:
        with db_session:
            if not date:
                date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            
            shifts = [
                ("Morning", date.replace(hour=6), date.replace(hour=14)),
                ("Afternoon", date.replace(hour=14), date.replace(hour=22)),
                ("Night", date.replace(hour=22), (date + timedelta(days=1)).replace(hour=6))
            ]
            
            summaries = []
            for shift_name, shift_start, shift_end in shifts:
                logs = select(l for l in ProductionLog 
                            if l.start_time >= shift_start and l.end_time <= shift_end)
                
                summaries.append(ShiftSummary(
                    shift=shift_name,
                    start_time=shift_start,
                    end_time=shift_end,
                    total_production=sum(l.quantity_completed + l.quantity_rejected for l in logs),
                    good_pieces=sum(l.quantity_completed for l in logs),
                    rejected_pieces=sum(l.quantity_rejected for l in logs),
                    downtime=calculate_shift_downtime(shift_start, shift_end),
                    operators=list(set(l.operator.username for l in logs)),
                    machines=list(set(f"{l.schedule_version.schedule_item.machine.work_center.code}-{l.schedule_version.schedule_item.machine.make}" for l in logs)),
                    efficiency=calculate_shift_efficiency(shift_start, shift_end)
                ))
            
            return summaries
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# @router.get("/quality-metrics/", response_model=QualityMetrics)
# async def get_quality_metrics(
#     start_date: datetime = Query(default=None),
#     end_date: datetime = Query(default=None)
# ):
#     """Get detailed quality metrics"""
#     try:
#         with db_session:
#             if not start_date:
#                 start_date = datetime.utcnow() - timedelta(days=7)
#             if not end_date:
#                 end_date = datetime.utcnow()

#             logs = select(l for l in ProductionLog 
#                          if l.start_time >= start_date and l.end_time <= end_date)
            
#             total_pieces = sum(l.quantity_completed + l.quantity_rejected for l in logs)
#             rejected_pieces = sum(l.quantity_rejected for l in logs)
            
#             return QualityMetrics(
#                 defect_rate=rejected_pieces / total_pieces * 100 if total_pieces > 0 else 0,
#                 rework_rate=calculate_rework_rate(),
#                 scrap_rate=calculate_scrap_rate(),
#                 first_pass_yield=calculate_first_pass_yield(),
#                 defect_categories=analyze_defect_categories(),
#                 quality_issues=get_recent_quality_issues()
#             )
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))

@router.get("/resource-utilization/", response_model=List[ResourceUtilization])
async def get_resource_utilization(
    start_date: datetime = Query(default=None),
    end_date: datetime = Query(default=None)
):
    """Get detailed resource utilization metrics"""
    try:
        with db_session:
            if not start_date:
                start_date = datetime.utcnow() - timedelta(days=1)
            if not end_date:
                end_date = datetime.utcnow()

            machines = select(m for m in Machine)
            utilization_data = []
            
            for machine in machines:
                logs = select(l for l in ProductionLog 
                            if l.schedule_version.schedule_item.machine == machine
                            and l.start_time >= start_date 
                            and l.end_time <= end_date)
                
                utilization_data.append(ResourceUtilization(
                    machine_id=machine.id,
                    machine_name=f"{machine.work_center.code}-{machine.make}",
                    utilization_rate=calculate_machine_utilization_rate(machine.id),
                    productive_time=calculate_productive_time(logs),
                    idle_time=calculate_idle_time(logs),
                    setup_time=calculate_machine_setup_time(logs),
                    breakdown_time=calculate_breakdown_time(logs),
                    maintenance_time=calculate_maintenance_time(logs)
                ))
            
            return utilization_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/production-trends/", response_model=List[ProductionTrend])
async def get_production_trends(
    start_date: datetime = Query(default=None),
    end_date: datetime = Query(default=None),
    interval: str = Query(default="hour")  # hour, day, week, month
):
    """Get production trends over time"""
    try:
        with db_session:
            if not start_date:
                start_date = datetime.utcnow() - timedelta(days=7)
            if not end_date:
                end_date = datetime.utcnow()

            trends = []
            current = start_date
            
            while current <= end_date:
                if interval == "hour":
                    next_interval = current + timedelta(hours=1)
                elif interval == "day":
                    next_interval = current + timedelta(days=1)
                elif interval == "week":
                    next_interval = current + timedelta(weeks=1)
                else:  # month
                    next_interval = (current.replace(day=1) + timedelta(days=32)).replace(day=1)

                logs = select(l for l in ProductionLog 
                            if l.start_time >= current and l.end_time < next_interval)
                
                trends.append(ProductionTrend(
                    timestamp=current,
                    production_rate=calculate_production_rate(logs),
                    quality_rate=calculate_quality_rate(logs),
                    machine_utilization=calculate_utilization_rate(logs)
                ))
                
                current = next_interval
            
            return trends
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))