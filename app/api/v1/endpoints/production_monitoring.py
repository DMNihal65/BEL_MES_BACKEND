from app.schemas.scheduled import ScheduledOperation, ProductionLogResponse, CombinedScheduleProductionResponse, MachineLiveStatus, MachineStatusHistory, MachineAnalytics, ProductionSummary, StatusChange, PartCount, ProgramChange, MachineSummary

from fastapi import APIRouter, HTTPException, Query, Path, Depends, status, WebSocket
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
from typing import List, Optional, Dict, Set
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
    calculate_quality_rate, calculate_utilization_rate,
    get_machine_current_status, get_machine_production_metrics, calculate_shift_metrics, get_production_trends
)
from app.models.production import MachineRaw, MachineRawLive, StatusLookup
from pydantic import ValidationError
import asyncio
from collections import defaultdict
import pandas as pd
from fastapi.websockets import WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import json

router = APIRouter(prefix="/production_monitoring", tags=["production_monitoring"])

# Add a class to manage WebSocket connections
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                # Remove dead connections
                self.active_connections.remove(connection)

manager = ConnectionManager()

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
@db_session
async def get_real_time_machine_status():
    """Get real-time status of all machines from MachineRawLive"""
    try:
        machines = select(m for m in Machine)
        machine_statuses = []
        current_time = datetime.utcnow()
        day_ago = current_time - timedelta(hours=24)
        
        for machine in machines:
            current_status = get_machine_current_status(machine.id)
            if current_status:
                try:
                    uptime = calculate_machine_uptime(
                        machine.id,
                        day_ago,
                        current_time
                    )
                    efficiency = calculate_machine_efficiency(
                        machine.id,
                        day_ago,
                        current_time
                    )
                except Exception as calc_error:
                    print(f"Error calculating metrics for machine {machine.id}: {str(calc_error)}")
                    uptime = 0.0
                    efficiency = 0.0

                machine_statuses.append(MachineStatus(
                    machine_id=machine.id,
                    machine_name=f"{machine.work_center.code}-{machine.make}",
                    status=current_status['status'],
                    current_order=current_status['program'],
                    current_operation=None,  # Can be mapped from program if needed
                    start_time=current_status['timestamp'],
                    uptime=uptime,
                    efficiency=efficiency
                ))
        
        return machine_statuses
    except Exception as e:
        print(f"Error in get_real_time_machine_status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/machine-metrics/{machine_id}", response_model=ProductionMetrics)
async def get_machine_metrics(
    machine_id: int,
    start_date: datetime = Query(default=None),
    end_date: datetime = Query(default=None)
):
    """Get detailed metrics for a specific machine"""
    try:
        with db_session:
            if not start_date:
                start_date = datetime.utcnow() - timedelta(hours=24)
            if not end_date:
                end_date = datetime.utcnow()

            metrics = get_machine_production_metrics(machine_id, start_date, end_date)
            
            return ProductionMetrics(
                oee=metrics['status_distribution'].get('PRODUCTION', 0),
                availability=metrics['status_distribution'].get('ON', 0),
                performance=calculate_machine_efficiency(machine_id, start_date, end_date),
                quality=95.0,  # This would need to be calculated from quality data
                total_planned_time=(end_date - start_date).total_seconds() / 3600,
                actual_runtime=metrics['status_distribution'].get('PRODUCTION', 0) * (end_date - start_date).total_seconds() / 3600 / 100,
                downtime=metrics['status_distribution'].get('OFF', 0) * (end_date - start_date).total_seconds() / 3600 / 100,
                ideal_cycle_time=0.0,  # Would need to be calculated from standard times
                actual_cycle_time=0.0,  # Would need to be calculated from actual production
                total_pieces=metrics['part_count'],
                good_pieces=metrics['part_count'],  # Would need quality data
                rejected_pieces=0  # Would need quality data
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/shift-summary/", response_model=List[ShiftSummary])
async def get_shift_summary(date: datetime = Query(default=None)):
    """Get production summary for each shift using actual machine data"""
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
                shift_metrics = calculate_shift_metrics(shift_start, shift_end)
                
                total_parts = sum(metrics['part_count'] for metrics in shift_metrics.values())
                avg_efficiency = sum(metrics['efficiency'] for metrics in shift_metrics.values()) / len(shift_metrics) if shift_metrics else 0
                
                summaries.append(ShiftSummary(
                    shift=shift_name,
                    start_time=shift_start,
                    end_time=shift_end,
                    total_production=total_parts,
                    good_pieces=total_parts,  # Would need quality data
                    rejected_pieces=0,  # Would need quality data
                    downtime=calculate_shift_downtime(shift_start, shift_end),
                    operators=[],  # Would need operator data
                    machines=[f"{m.work_center.code}-{m.make}" for m in Machine.select()],
                    efficiency=avg_efficiency
                ))
            
            return summaries
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/production-trends/", response_model=List[ProductionTrend])
async def get_production_trends(
    start_date: datetime = Query(default=None),
    end_date: datetime = Query(default=None),
    interval: str = Query(default="hour")
):
    """Get production trends using actual machine data"""
    try:
        with db_session:
            if not start_date:
                start_date = datetime.utcnow() - timedelta(days=7)
            if not end_date:
                end_date = datetime.utcnow()

            interval_minutes = {
                "hour": 60,
                "day": 1440,
                "week": 10080,
                "month": 43200
            }.get(interval, 60)

            trends_data = get_production_trends(start_date, end_date, interval_minutes)
            
            return [
                ProductionTrend(
                    timestamp=trend['timestamp'],
                    production_rate=sum(m['part_count'] for m in trend['machines'].values()),
                    quality_rate=95.0,  # Would need quality data
                    machine_utilization=sum(m['efficiency'] for m in trend['machines'].values()) / len(trend['machines']) if trend['machines'] else 0
                )
                for trend in trends_data
            ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/production-logs/", response_model=List[ProductionLogResponse])
async def get_production_logs(
    machine_id: Optional[int] = Query(None, description="Filter by machine ID"),
    operator_id: Optional[int] = Query(None, description="Filter by operator ID"),
    schedule_version_id: Optional[int] = Query(None, description="Filter by schedule version ID"),
    start_date: Optional[datetime] = Query(None, description="Filter logs after this date"),
    end_date: Optional[datetime] = Query(None, description="Filter logs before this date"),
    limit: int = Query(50, ge=1, le=100, description="Number of logs to return")
):
    """
    Retrieve production logs with optional filtering by machine, operator, schedule version, and date range.
    """
    try:
        with db_session:
            # Base query
            query = select(log for log in ProductionLog)
            
            # Apply filters
            if machine_id is not None:
                query = query.filter(lambda l: l.machine_id == machine_id)
            
            if operator_id is not None:
                query = query.filter(lambda l: l.operator.id == operator_id)
                
            if schedule_version_id is not None:
                query = query.filter(lambda l: l.schedule_version.id == schedule_version_id)
                
            if start_date is not None:
                query = query.filter(lambda l: l.start_time >= start_date)
                
            if end_date is not None:
                query = query.filter(lambda l: l.end_time <= end_date)
            
            # Order by most recent first
            query = query.order_by(desc(ProductionLog.start_time))
            
            # Execute query with limit
            logs = query.limit(limit)[:]
            
            # Transform to response model
            response_logs = []
            for log in logs:
                try:
                    # Get related data safely
                    machine_name = None
                    if log.machine_id:
                        machine = Machine.get(id=log.machine_id)
                        if machine and hasattr(machine, 'work_center'):
                            machine_name = f"{machine.work_center.code}-{machine.make}"
                    
                    schedule_info = log.schedule_version
                    part_number = None
                    operation_description = None
                    version_number = None
                    scheduled_item_id = None
                    production_order = None
                    
                    if schedule_info:
                        schedule_item = schedule_info.schedule_item
                        if schedule_item:
                            scheduled_item_id = schedule_item.id
                            if schedule_item.order:
                                part_number = schedule_item.order.part_number
                                production_order = schedule_item.order.production_order
                            if schedule_item.operation:
                                operation_description = schedule_item.operation.operation_description
                        version_number = schedule_info.version_number
                    
                    response_log = ProductionLogResponse(
                        id=log.id,
                        operator_id=log.operator.id if log.operator else None,
                        operator_name=log.operator.username if log.operator else None,
                        start_time=log.start_time,
                        end_time=log.end_time,
                        quantity_completed=log.quantity_completed or 0,
                        quantity_rejected=log.quantity_rejected or 0,
                        part_number=part_number,
                        operation_description=operation_description,
                        machine_name=machine_name,
                        notes=log.notes,
                        version_number=version_number,
                        scheduled_item_id=scheduled_item_id,
                        production_order=production_order
                    )
                    response_logs.append(response_log)
                    
                except ValidationError as ve:
                    print(f"Validation error for log {log.id}: {str(ve)}")
                    continue
                
            return response_logs
            
    except Exception as e:
        error_msg = f"Error retrieving production logs: {str(e)}"
        print(error_msg)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "Failed to retrieve production logs",
                "error": str(e)
            }
        )

@router.get("/combined-schedule-production/", response_model=CombinedScheduleProductionResponse)
async def get_combined_schedule_production(
    start_date: Optional[datetime] = Query(None, description="Filter from this date"),
    end_date: Optional[datetime] = Query(None, description="Filter until this date"),
    machine_id: Optional[int] = Query(None, description="Filter by machine ID"),
    operator_id: Optional[int] = Query(None, description="Filter by operator ID")
):
    """
    Retrieve combined production logs and scheduled operations with optional filtering
    """
    try:
        with db_session:
            # Get production logs using existing function
            logs_query = select(log for log in ProductionLog)
            
            if start_date:
                logs_query = logs_query.filter(lambda l: l.start_time >= start_date)
            if end_date:
                logs_query = logs_query.filter(lambda l: l.end_time <= end_date)
            if machine_id:
                logs_query = logs_query.filter(lambda l: l.machine_id == machine_id)
            if operator_id:
                logs_query = logs_query.filter(lambda l: l.operator.id == operator_id)
                
            logs = logs_query.order_by(desc(ProductionLog.start_time))[:]
            
            # Process logs similar to get_production_logs endpoint
            logs_data = []
            for log in logs:
                try:
                    machine_name = None
                    if log.machine_id:
                        machine = Machine.get(id=log.machine_id)
                        if machine and hasattr(machine, 'work_center'):
                            machine_name = f"{machine.work_center.code}-{machine.make}"
                    
                    schedule_info = log.schedule_version
                    part_number = None
                    operation_description = None
                    version_number = None
                    scheduled_item_id = None
                    production_order = None
                    
                    if schedule_info:
                        schedule_item = schedule_info.schedule_item
                        if schedule_item:
                            scheduled_item_id = schedule_item.id
                            if schedule_item.order:
                                part_number = schedule_item.order.part_number
                                production_order = schedule_item.order.production_order
                            if schedule_item.operation:
                                operation_description = schedule_item.operation.operation_description
                        version_number = schedule_info.version_number
                    
                    logs_data.append(ProductionLogResponse(
                        id=log.id,
                        operator_id=log.operator.id if log.operator else None,
                        operator_name=log.operator.username if log.operator else None,
                        start_time=log.start_time,
                        end_time=log.end_time,
                        quantity_completed=log.quantity_completed or 0,
                        quantity_rejected=log.quantity_rejected or 0,
                        part_number=part_number,
                        operation_description=operation_description,
                        machine_name=machine_name,
                        notes=log.notes,
                        version_number=version_number,
                        scheduled_item_id=scheduled_item_id,
                        production_order=production_order
                    ))
                except ValidationError as ve:
                    print(f"Validation error for log {log.id}: {str(ve)}")
                    continue

            # Get scheduled operations
            df = fetch_operations()
            component_quantities = fetch_component_quantities()
            lead_times = fetch_lead_times()

            schedule_df, overall_end_time, overall_time, daily_production, _, _ = schedule_operations(
                df, component_quantities, lead_times
            )

            scheduled_operations = []
            if not schedule_df.empty:
                machine_details = {
                    machine.id: f"{machine.work_center.code}-{machine.make}"
                    for machine in Machine.select()
                }

                for _, row in schedule_df.iterrows():
                    total_qty, current_qty, today_qty = extract_quantity(row['quantity'])
                    
                    if total_qty > 1:  # Skip setup operations
                        quantity_str = f"Process({current_qty}/{total_qty}pcs, Today: {today_qty}pcs)"
                        scheduled_operations.append(
                            ScheduledOperation(
                                component=row['partno'],
                                description=row['operation'],
                                machine=machine_details.get(row['machine_id'], f"Machine-{row['machine_id']}"),
                                start_time=row['start_time'],
                                end_time=row['end_time'],
                                quantity=quantity_str,
                                production_order=row.get('production_order', '')
                            )
                        )

            return CombinedScheduleProductionResponse(
                production_logs=logs_data,
                scheduled_operations=scheduled_operations
            )

    except Exception as e:
        error_msg = f"Error retrieving combined schedule and production data: {str(e)}"
        print(error_msg)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "Failed to retrieve combined data",
                "error": str(e)
            }
        )

# Live Machine Status Endpoint
@router.get("/live-status/", response_model=List[MachineLiveStatus])
async def get_live_machine_status():
    """
    Get current status of all machines from MachineRawLive
    """
    try:
        with db_session:
            live_statuses = []
            machines = select(m for m in Machine)[:]
            
            for machine in machines:
                try:
                    live_data = MachineRawLive.get(machine_id=machine.id)
                    if live_data:
                        print(f"Processing machine {machine.id}")  # Debug log
                        
                        # Base machine data
                        machine_data = {
                            "machine_id": machine.id,
                            "machine_name": f"{machine.work_center.code}-{machine.make}",
                            "status": live_data.status.status_name,
                            "program_number": live_data.program_number or "",
                            "active_program": live_data.active_program or "",
                            "selected_program": live_data.selected_program or "",
                            "part_count": live_data.part_count or 0,
                            "job_status": live_data.job_status,
                            "last_updated": live_data.time_stamp,
                            "job_in_progress": live_data.job_in_progress,
                            # Initialize order details with default values
                            "production_order": None,
                            "part_number": None,
                            "part_description": None,
                            "required_quantity": None,
                            "launched_quantity": None,
                            "operation_number": None,
                            "operation_description": None
                        }
                        
                        # Get order details if job is in progress
                        if live_data.job_in_progress:
                            print(f"Getting order details for job {live_data.job_in_progress}")  # Debug log
                            order_details = live_data.get_order_details()
                            if order_details:
                                print(f"Found order details: {order_details}")  # Debug log
                                machine_data.update(order_details)
                            else:
                                print(f"No order details found for job {live_data.job_in_progress}")
                        
                        live_statuses.append(MachineLiveStatus(**machine_data))
                        print(f"Successfully added machine {machine.id} to response")  # Debug log
                
                except Exception as machine_error:
                    print(f"Error processing machine {machine.id}: {str(machine_error)}")
                    continue
            
            print(f"Returning {len(live_statuses)} machine statuses")  # Debug log
            return live_statuses
            
    except Exception as e:
        print(f"Error in get_live_machine_status: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching live status: {str(e)}"
        )

# WebSocket endpoint for live machine status
@router.websocket("/ws/live-status/")
async def websocket_live_status(websocket: WebSocket):
    """
    WebSocket endpoint for real-time machine status updates
    """
    try:
        await manager.connect(websocket)
        print(f"Client connected to WebSocket. Total connections: {len(manager.active_connections)}")
        
        while websocket in manager.active_connections:
            try:
                with db_session:
                    machine_statuses = list(MachineRawLive.select()[:])
                    print(f"Found {len(machine_statuses)} machine statuses")
                    
                    response_data = []
                    for status in machine_statuses:
                        try:
                            machine = Machine.get(id=status.machine_id)
                            if not machine:
                                print(f"Machine not found for ID: {status.machine_id}")
                                continue

                            # Base machine data - match the structure of GET endpoint
                            machine_data = {
                                "machine_id": status.machine_id,
                                "machine_name": f"{machine.work_center.code}-{machine.make}",
                                "status": status.status.status_name,
                                "program_number": status.program_number or "",
                                "active_program": status.active_program or "",
                                "selected_program": status.selected_program or "",
                                "part_count": status.part_count or 0,
                                "job_status": status.job_status,
                                "last_updated": status.time_stamp.isoformat() if status.time_stamp else None,  # Convert datetime to ISO string
                                "job_in_progress": status.job_in_progress,
                                # Initialize order details with default values
                                "production_order": None,
                                "part_number": None,
                                "part_description": None,
                                "required_quantity": None,
                                "launched_quantity": None,
                                "operation_number": None,
                                "operation_description": None
                            }
                            
                            # Get order details if job is in progress
                            if status.job_in_progress:
                                print(f"Getting order details for job {status.job_in_progress}")
                                order_details = status.get_order_details()
                                if order_details:
                                    print(f"Found order details: {order_details}")
                                    machine_data.update(order_details)
                                else:
                                    print(f"No order details found for job {status.job_in_progress}")
                            
                            response_data.append(machine_data)
                            
                        except Exception as machine_error:
                            print(f"Error processing machine status: {str(machine_error)}")
                            continue
                    
                    if websocket in manager.active_connections:
                        try:
                            print(f"Sending {len(response_data)} machine statuses")
                            await websocket.send_json(response_data)
                        except RuntimeError as send_error:
                            print(f"Error sending data: {str(send_error)}")
                            break
                        except Exception as send_error:
                            print(f"Unexpected error sending data: {str(send_error)}")
                            break
                
            except Exception as loop_error:
                print(f"Error in WebSocket loop: {str(loop_error)}")
                if "close message" in str(loop_error).lower():
                    break
                continue
                
            await asyncio.sleep(1)  # Update interval
            
    except WebSocketDisconnect:
        print("Client disconnected from WebSocket")
    except Exception as e:
        print(f"WebSocket error: {str(e)}")
    finally:
        if websocket in manager.active_connections:
            manager.disconnect(websocket)
            print("Cleaned up WebSocket connection")

# Machine History and Analytics
@router.get("/machine-history/{machine_id}", response_model=MachineStatusHistory)
async def get_machine_history(
    machine_id: int,
    start_date: datetime = Query(default=None),
    end_date: datetime = Query(default=None)
):
    """
    Get detailed machine history with organized data for various graphs
    """
    try:
        with db_session:
            if not start_date:
                start_date = datetime.utcnow() - timedelta(days=1)
            if not end_date:
                end_date = datetime.utcnow()

            machine = Machine.get(id=machine_id)
            if not machine:
                raise HTTPException(status_code=404, detail="Machine not found")

            # Get all records for the time period
            history_records = select(r for r in MachineRaw 
                                  if r.machine_id == machine_id 
                                  and r.time_stamp >= start_date 
                                  and r.time_stamp <= end_date
                                  ).order_by(lambda r: r.time_stamp)[:]

            # Initialize data structures
            status_changes = []
            part_counts = []
            programs = []
            hourly_production = defaultdict(int)
            status_duration = defaultdict(int)
            current_status = None
            status_start_time = None

            for i, record in enumerate(history_records):
                # Track status changes with duration
                if current_status != record.status.status_name:
                    if current_status and status_start_time:
                        duration = (record.time_stamp - status_start_time).total_seconds() / 3600  # hours
                        status_duration[current_status] += duration
                    
                    current_status = record.status.status_name
                    status_start_time = record.time_stamp
                    status_changes.append(StatusChange(
                        timestamp=record.time_stamp,
                        status=record.status.status_name,
                        program=record.active_program
                    ))

                # Track part count changes
                if record.part_count is not None:
                    part_counts.append(PartCount(
                        timestamp=record.time_stamp,
                        count=record.part_count
                    ))
                    # Add to hourly production
                    hour_key = record.time_stamp.replace(minute=0, second=0, microsecond=0)
                    hourly_production[hour_key] = record.part_count

                # Track program changes
                if record.active_program:
                    programs.append(ProgramChange(
                        timestamp=record.time_stamp,
                        program=record.active_program
                    ))

            # Calculate final status duration if needed
            if current_status and status_start_time and history_records:
                duration = (history_records[-1].time_stamp - status_start_time).total_seconds() / 3600
                status_duration[current_status] += duration

            return MachineStatusHistory(
                machine_id=machine_id,
                machine_name=f"{machine.work_center.code}-{machine.make}",
                start_date=start_date,
                end_date=end_date,
                status_changes=status_changes,
                part_counts=part_counts,
                programs=programs,
                hourly_production=dict(hourly_production),
                status_duration=dict(status_duration)
            )
            
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching machine history: {str(e)}"
        )

# Production Analytics Dashboard
@router.get("/production-analytics/", response_model=List[MachineAnalytics])
async def get_production_analytics(
    start_date: datetime = Query(default=None),
    end_date: datetime = Query(default=None)
):
    """
    Get comprehensive production analytics for all machines
    """
    try:
        with db_session:
            if not start_date:
                start_date = datetime.utcnow() - timedelta(days=1)
            if not end_date:
                end_date = datetime.utcnow()

            machines = select(m for m in Machine)[:]
            analytics = []

            for machine in machines:
                try:
                    # Get all records for the machine within time range
                    machine_records = list(select(r for r in MachineRaw
                        if r.machine_id == machine.id
                        and r.time_stamp >= start_date
                        and r.time_stamp <= end_date
                    ).order_by(lambda r: r.time_stamp)[:])

                    # Calculate status distribution
                    status_counts = defaultdict(int)
                    total_records = len(machine_records)
                    
                    for record in machine_records:
                        if record and record.status and record.status.status_name:
                            status_counts[record.status.status_name] += 1

                    status_distribution = {}
                    if total_records > 0:
                        for status, count in status_counts.items():
                            status_distribution[status] = (count / total_records) * 100

                    # Calculate production trends
                    production_data = []
                    for record in machine_records:
                        if record and hasattr(record, 'part_count') and record.part_count is not None:
                            production_data.append({
                                'timestamp': record.time_stamp,
                                'part_count': record.part_count
                            })

                    # Create hourly production trends
                    production_trends = []
                    if production_data:
                        df = pd.DataFrame(production_data)
                        if not df.empty:
                            df.set_index('timestamp', inplace=True)
                            hourly_production = df.resample('H').last()
                            hourly_production = hourly_production.fillna(method='ffill')
                            
                            for idx, row in hourly_production.iterrows():
                                if pd.notnull(row['part_count']):
                                    production_trends.append(
                                        ProductionTrend(
                                            timestamp=idx,
                                            production_rate=float(row['part_count']),
                                            quality_rate=100.0,  # Default value
                                            machine_utilization=100.0  # Default value
                                        )
                                    )

                    # Calculate total parts
                    total_parts = 0
                    if machine_records:
                        part_counts = [r.part_count for r in machine_records if r and hasattr(r, 'part_count') and r.part_count is not None]
                        if part_counts:
                            total_parts = max(part_counts)

                    # Calculate average cycle time
                    avg_cycle_time = calculate_average_cycle_time(machine_records)

                    analytics.append(MachineAnalytics(
                        machine_id=machine.id,
                        machine_name=f"{machine.work_center.code}-{machine.make}",
                        status_distribution=status_distribution,
                        production_trends=production_trends,
                        total_parts=total_parts,
                        uptime_percentage=status_distribution.get("RUNNING", 0.0),
                        average_cycle_time=avg_cycle_time
                    ))

                except Exception as machine_error:
                    print(f"Error processing machine {machine.id}: {str(machine_error)}")
                    continue

            return analytics

    except Exception as e:
        print(f"Error in production analytics: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching production analytics: {str(e)}"
        )

def calculate_average_cycle_time(production_records):
    """Helper function to calculate average cycle time"""
    try:
        if not production_records:
            return 0.0
        
        cycle_times = []
        prev_record = None
        
        for record in production_records:
            if (prev_record and 
                hasattr(prev_record, 'part_count') and 
                hasattr(record, 'part_count') and 
                prev_record.part_count is not None and 
                record.part_count is not None):
                
                if record.part_count > prev_record.part_count:
                    time_diff = (record.time_stamp - prev_record.time_stamp).total_seconds()
                    part_diff = record.part_count - prev_record.part_count
                    if part_diff > 0:
                        cycle_times.append(time_diff / part_diff)
            prev_record = record
        
        return sum(cycle_times) / len(cycle_times) if cycle_times else 0.0
        
    except Exception as e:
        print(f"Error calculating cycle time: {str(e)}")
        return 0.0

# Production Summary
@router.get("/production-summary/", response_model=ProductionSummary)
async def get_production_summary(
    start_date: datetime = Query(default=None),
    end_date: datetime = Query(default=None)
):
    """
    Get overall production summary across all machines
    """
    try:
        with db_session:
            if not start_date:
                start_date = datetime.utcnow() - timedelta(days=1)
            if not end_date:
                end_date = datetime.utcnow()

            machines = select(m for m in Machine)[:]
            total_production = 0
            machine_summaries = []
            overall_status_distribution = defaultdict(int)

            for machine in machines:
                # Corrected records query
                records = select(r for r in MachineRaw
                    if r.machine_id == machine.id
                    and r.time_stamp >= start_date
                    and r.time_stamp <= end_date
                )[:]

                # Calculate machine-specific metrics
                machine_production = sum(r.part_count or 0 for r in records)
                total_production += machine_production

                status_counts = defaultdict(int)
                for record in records:
                    status_counts[record.status.status_name] += 1
                    overall_status_distribution[record.status.status_name] += 1

                total_records = len(records)
                machine_summaries.append({
                    "machine_id": machine.id,
                    "machine_name": f"{machine.work_center.code}-{machine.make}",
                    "total_production": machine_production,
                    "status_distribution": {
                        status: (count / total_records * 100) if total_records > 0 else 0
                        for status, count in status_counts.items()
                    }
                })

            # Calculate overall status distribution
            total_overall_records = sum(overall_status_distribution.values())
            overall_distribution = {
                status: (count / total_overall_records * 100) if total_overall_records > 0 else 0
                for status, count in overall_status_distribution.items()
            }

            return ProductionSummary(
                start_date=start_date,
                end_date=end_date,
                total_production=total_production,
                machine_summaries=machine_summaries,
                overall_status_distribution=overall_distribution
            )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching production summary: {str(e)}"
        )