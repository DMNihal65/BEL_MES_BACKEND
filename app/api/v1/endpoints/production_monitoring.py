from app.schemas.scheduled import ScheduledOperation, ProductionLogResponse, CombinedScheduleProductionResponse, \
    MachineLiveStatus, MachineStatusHistory, MachineAnalytics, ProductionSummary, StatusChange, PartCount, \
    ProgramChange, MachineSummary, OrderProductionAnalysis, ShiftPerformanceAnalysis, ProductionKPIDashboard, \
    MachineOEEAnalysis, DetailedShiftSummary, MachineStatusTimeline, DailyComparison, MachineComparison, \
    ProductionComparison, ProductionTrend, DailyProductionComparison, ProductionDateRange, DailyProductionData, \
    DailyMachineProduction, OEEMetrics, LossAnalysis, OEETrend, OEELosses, OverallOEEAnalysis

from fastapi import APIRouter, HTTPException, Query, Path, Depends, status, WebSocket
from pony.orm import db_session, select, avg, count, desc
from app.schemas.scheduled import ScheduledOperation, ScheduleResponse, ProductionMetrics, MachineStatus, ProductionKPI, \
    ShiftSummary, ProductionTrend, QualityMetrics, ResourceUtilization
from app.models import Order, Operation, Machine, PartScheduleStatus, PlannedScheduleItem, ScheduleVersion, Program
from app.crud.operation import fetch_operations
from app.crud.component_quantities import fetch_component_quantities
from app.crud.leadtime import fetch_lead_times
from app.algorithm.scheduling import schedule_operations
import re
from app.models import ProductionLog
from datetime import datetime, timedelta, date
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
from app.models.production import MachineRaw, MachineRawLive, StatusLookup, ShiftInfo, ShiftSummary as ShiftSummaryModel
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
                actual_runtime=metrics['status_distribution'].get('PRODUCTION', 0) * (
                            end_date - start_date).total_seconds() / 3600 / 100,
                downtime=metrics['status_distribution'].get('OFF', 0) * (
                            end_date - start_date).total_seconds() / 3600 / 100,
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
                avg_efficiency = sum(metrics['efficiency'] for metrics in shift_metrics.values()) / len(
                    shift_metrics) if shift_metrics else 0

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
                    machine_utilization=sum(m['efficiency'] for m in trend['machines'].values()) / len(
                        trend['machines']) if trend['machines'] else 0
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

                        # Ensure the work_center attribute exists before accessing it
                        if not hasattr(machine, 'work_center'):
                            print(f"Warning: Machine {machine.id} has no work_center attribute")
                            machine_name = f"Unknown-{machine.id}"
                        else:
                            machine_name = f"{machine.work_center.code}-{machine.make}"

                        # Base machine data
                        machine_data = {
                            "machine_id": machine.id,
                            "machine_name": machine_name,
                            "status": live_data.status.status_name,
                            "program_number": live_data.selected_program or "",
                            "active_program": live_data.active_program or "",
                            "selected_program": live_data.selected_program or "",
                            "part_count": live_data.part_count or 0,
                            "job_status": live_data.job_status,
                            "last_updated": live_data.timestamp.isoformat() if live_data.timestamp else None,
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

                        # Get order details using the enhanced method
                        if live_data.job_in_progress or live_data.active_program:
                            try:
                                order_details = live_data.get_order_details()
                                if order_details:
                                    print(f"Found order details for machine {machine.id}")
                                    machine_data.update(order_details)
                                else:
                                    print(f"No order details found for machine {machine.id}")
                            except Exception as e:
                                print(f"Error getting order details for machine {machine.id}: {str(e)}")
                                import traceback
                                print(traceback.format_exc())

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
                            # First make sure this machine exists in the database
                            machine = Machine.get(id=status.machine_id)
                            if not machine:
                                print(f"Machine not found for ID: {status.machine_id}")
                                # Still include this machine in the response with basic info
                                machine_data = {
                                    "machine_id": status.machine_id,
                                    "machine_name": f"Unknown-{status.machine_id}",
                                    "status": status.status.status_name,
                                    "program_number": status.selected_program or "",
                                    "active_program": status.active_program or "",
                                    "selected_program": status.selected_program or "",
                                    "part_count": status.part_count or 0,
                                    "job_status": status.job_status,
                                    "last_updated": status.timestamp.isoformat() if status.timestamp else None,
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
                                response_data.append(machine_data)
                                continue

                            # Base machine data
                            machine_data = {
                                "machine_id": status.machine_id,
                                "machine_name": f"{machine.work_center.code}-{machine.make}",
                                "status": status.status.status_name,
                                "program_number": status.selected_program or "",
                                "active_program": status.active_program or "",
                                "selected_program": status.selected_program or "",
                                "part_count": status.part_count or 0,
                                "job_status": status.job_status,
                                "last_updated": status.timestamp.isoformat() if status.timestamp else None,
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
                            if status.job_in_progress or status.active_program:
                                try:
                                    # Use the enhanced method in MachineRawLive that now handles both approaches
                                    order_details = status.get_order_details()
                                    if order_details:
                                        print(f"Successfully found order details via get_order_details")
                                        machine_data.update(order_details)
                                    else:
                                        print(f"No order details found for machine {machine.id}")
                                except Exception as detail_error:
                                    print(f"Error getting order details: {str(detail_error)}")
                                    import traceback
                                    print(traceback.format_exc())

                            response_data.append(machine_data)

                        except Exception as machine_error:
                            print(f"Error processing machine status: {str(machine_error)}")
                            continue

                    if websocket in manager.active_connections and response_data:
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

            await asyncio.sleep(5)  # Update interval is 5 seconds

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
                                     and r.timestamp >= start_date
                                     and r.timestamp <= end_date
                                     ).order_by(lambda r: r.timestamp)[:]

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
                        duration = (record.timestamp - status_start_time).total_seconds() / 3600  # hours
                        status_duration[current_status] += duration

                    current_status = record.status.status_name
                    status_start_time = record.timestamp
                    status_changes.append(StatusChange(
                        timestamp=record.timestamp,
                        status=record.status.status_name,
                        program=record.active_program
                    ))

                # Track part count changes
                if record.part_count is not None:
                    part_counts.append(PartCount(
                        timestamp=record.timestamp,
                        count=record.part_count
                    ))
                    # Add to hourly production
                    hour_key = record.timestamp.replace(minute=0, second=0, microsecond=0)
                    hourly_production[hour_key] = record.part_count

                # Track program changes
                if record.active_program:
                    programs.append(ProgramChange(
                        timestamp=record.timestamp,
                        program=record.active_program
                    ))

            # Calculate final status duration if needed
            if current_status and status_start_time and history_records:
                duration = (history_records[-1].timestamp - status_start_time).total_seconds() / 3600
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
                                                  and r.timestamp >= start_date
                                                  and r.timestamp <= end_date
                                                  ).order_by(lambda r: r.timestamp)[:])

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
                                'timestamp': record.timestamp,
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
                        part_counts = [r.part_count for r in machine_records if
                                       r and hasattr(r, 'part_count') and r.part_count is not None]
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
                    time_diff = (record.timestamp - prev_record.timestamp).total_seconds()
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
, status=None):
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
                                 and r.timestamp >= start_date
                                 and r.timestamp <= end_date
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


@router.get("/order-production-analysis/{production_order}", response_model=OrderProductionAnalysis)
async def get_order_production_analysis(
        production_order: str,
        start_date: Optional[datetime] = Query(None),
        end_date: Optional[datetime] = Query(None)
):
    """
    Get detailed production analysis for a specific production order including:
    - Daily production progress
    - Machine-wise split of production
    - Quality metrics
    - Time analysis (setup time, production time, downtime)
    - Comparison with planned schedule
    """
    try:
        with db_session:
            # Get order details
            order = Order.get(production_order=production_order)
            if not order:
                raise HTTPException(status_code=404, detail="Order not found")

            # If start_date is not provided, get it from the earliest production log or planned schedule
            if not start_date:
                # Try to get earliest date from production logs
                earliest_log = select(l for l in ProductionLog if
                                      l.schedule_version and
                                      l.schedule_version.schedule_item.order.production_order == production_order
                                      ).order_by(lambda l: l.start_time).first()

                # Also check planned schedule items
                earliest_planned = select(p for p in PlannedScheduleItem if p.order == order
                                          ).order_by(lambda p: p.initial_start_time).first()

                if earliest_log and earliest_planned:
                    start_date = min(earliest_log.start_time, earliest_planned.initial_start_time)
                elif earliest_log:
                    start_date = earliest_log.start_time
                elif earliest_planned:
                    start_date = earliest_planned.initial_start_time
                else:
                    # If no dates found, default to 30 days ago
                    start_date = datetime.utcnow() - timedelta(days=30)

            if not end_date:
                end_date = datetime.utcnow()

            # Get production logs for this order
            logs = select(l for l in ProductionLog if
                          l.schedule_version and
                          l.schedule_version.schedule_item.order.production_order == production_order and
                          l.start_time >= start_date and
                          l.end_time <= end_date)

            # Calculate daily production
            daily_production = defaultdict(int)
            total_completed = 0
            total_rejected = 0
            machine_wise_production = defaultdict(lambda: {"completed": 0, "rejected": 0, "runtime": 0})
            setup_times = []
            production_times = []

            for log in logs:
                day = log.end_time.date()
                daily_production[day] += log.quantity_completed or 0
                total_completed += log.quantity_completed or 0
                total_rejected += log.quantity_rejected or 0

                if log.machine_id:
                    machine = Machine.get(id=log.machine_id)
                    if machine:
                        machine_name = f"{machine.work_center.code}-{machine.make}"
                        machine_wise_production[machine_name]["completed"] += log.quantity_completed or 0
                        machine_wise_production[machine_name]["rejected"] += log.quantity_rejected or 0
                        if log.start_time and log.end_time:
                            runtime = (log.end_time - log.start_time).total_seconds() / 3600
                            machine_wise_production[machine_name]["runtime"] += runtime

                # Calculate setup and production times
                if log.start_time and log.end_time:
                    duration = (log.end_time - log.start_time).total_seconds() / 3600
                    if log.quantity_completed == 1:  # Setup operation
                        setup_times.append(duration)
                    else:
                        production_times.append(duration)

            # Get planned schedule data
            planned_items = select(p for p in PlannedScheduleItem if p.order == order)
            planned_vs_actual = []

            for item in planned_items:
                latest_version = select(v for v in item.schedule_versions if v.is_active).first()
                if latest_version:
                    planned_vs_actual.append({
                        "operation": item.operation.operation_description,
                        "planned_quantity": latest_version.planned_quantity,
                        "completed_quantity": latest_version.completed_quantity,
                        "planned_start": latest_version.planned_start_time,
                        "planned_end": latest_version.planned_end_time,
                        "efficiency": (latest_version.completed_quantity / latest_version.planned_quantity * 100)
                        if latest_version.planned_quantity > 0 else 0
                    })

            return {
                "production_order": production_order,
                "part_number": order.part_number,
                "part_description": order.part_description,
                "total_completed": total_completed,
                "total_rejected": total_rejected,
                "quality_rate": ((total_completed - total_rejected) / total_completed * 100)
                if total_completed > 0 else 0,
                "daily_production": dict(daily_production),
                "machine_wise_production": dict(machine_wise_production),
                "average_setup_time": sum(setup_times) / len(setup_times) if setup_times else 0,
                "average_production_time": sum(production_times) / len(production_times) if production_times else 0,
                "planned_vs_actual": planned_vs_actual
            }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error analyzing order production: {str(e)}"
        )


@router.get("/shift-performance-analysis/", response_model=List[ShiftPerformanceAnalysis])
async def get_shift_performance_analysis(
        start_date: datetime = Query(default=None),
        end_date: datetime = Query(default=None),
        machine_id: Optional[int] = Query(None)
, status=None):
    """
    Get detailed shift-wise performance analysis including:
    - Production metrics per shift
    - Operator performance
    - Machine utilization
    - Quality metrics
    - Downtime analysis
    """
    try:
        with db_session:
            if not start_date:
                start_date = datetime.utcnow() - timedelta(days=7)
            if not end_date:
                end_date = datetime.utcnow()

            # Get shift timings
            shifts = select(s for s in ShiftInfo)[:]
            shift_analysis = []

            for shift in shifts:
                # Calculate shift windows for the date range
                current_date = start_date
                while current_date <= end_date:
                    shift_start = datetime.combine(current_date.date(), shift.start_time)
                    shift_end = datetime.combine(current_date.date(), shift.end_time)
                    if shift.end_time < shift.start_time:
                        shift_end += timedelta(days=1)

                    # Get production logs for this shift
                    query = select(l for l in ProductionLog if
                                   l.start_time >= shift_start and
                                   l.end_time <= shift_end)
                    if machine_id:
                        query = query.filter(lambda l: l.machine_id == machine_id)

                    logs = query[:]

                    # Calculate metrics
                    total_completed = sum(log.quantity_completed or 0 for log in logs)
                    total_rejected = sum(log.quantity_rejected or 0 for log in logs)

                    # Operator performance
                    operator_performance = defaultdict(lambda: {"completed": 0, "rejected": 0})
                    for log in logs:
                        if log.operator:
                            operator_performance[log.operator.username]["completed"] += log.quantity_completed or 0
                            operator_performance[log.operator.username]["rejected"] += log.quantity_rejected or 0

                    # Machine utilization
                    machine_utilization = {}
                    if machine_id:
                        machine_records = select(r for r in MachineRaw if
                                                 r.machine_id == machine_id and
                                                 r.timestamp >= shift_start and
                                                 r.timestamp <= shift_end)[:]

                        status_duration = defaultdict(int)
                        for i in range(len(machine_records) - 1):
                            duration = (machine_records[i + 1].timestamp - machine_records[
                                i].timestamp).total_seconds()
                            status_duration[machine_records[i].status.status_name] += duration

                        total_time = sum(status_duration.values())
                        if total_time > 0:
                            machine_utilization = {
                                status: (duration / total_time * 100)
                                for status, duration in status_duration.items()
                            }

                    shift_analysis.append({
                        "shift_date": current_date.date(),
                        "shift_start": shift_start,
                        "shift_end": shift_end,
                        "total_production": total_completed,
                        "total_rejected": total_rejected,
                        "quality_rate": ((total_completed - total_rejected) / total_completed * 100)
                        if total_completed > 0 else 0,
                        "operator_performance": dict(operator_performance),
                        "machine_utilization": machine_utilization
                    })

                    current_date += timedelta(days=1)

            return shift_analysis

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error analyzing shift performance: {str(e)}"
        )


@router.get("/production-kpi-dashboard/", response_model=ProductionKPIDashboard)
async def get_production_kpi_dashboard(
        start_date: datetime = Query(default=None),
        end_date: datetime = Query(default=None)
, status=None):
    """
    Get comprehensive production KPIs including:
    - Overall plant efficiency
    - Machine-wise OEE
    - Production target vs actual
    - Quality metrics
    - Bottleneck analysis
    - Cost of poor quality
    """
    try:
        with db_session:
            if not start_date:
                start_date = datetime.utcnow() - timedelta(days=30)
            if not end_date:
                end_date = datetime.utcnow()

            machines = select(m for m in Machine)[:]
            machine_kpis = []
            total_production = 0
            total_rejected = 0
            total_planned_time = 0
            total_actual_runtime = 0

            for machine in machines:
                # Get production logs
                logs = select(l for l in ProductionLog if
                              l.machine_id == machine.id and
                              l.start_time >= start_date and
                              l.end_time <= end_date)[:]

                # Calculate machine KPIs
                completed = sum(log.quantity_completed or 0 for log in logs)
                rejected = sum(log.quantity_rejected or 0 for log in logs)
                total_production += completed
                total_rejected += rejected

                # Calculate machine utilization
                machine_records = select(r for r in MachineRaw if
                                         r.machine_id == machine.id and
                                         r.timestamp >= start_date and
                                         r.timestamp <= end_date)[:]

                status_duration = defaultdict(int)
                for i in range(len(machine_records) - 1):
                    duration = (machine_records[i + 1].timestamp - machine_records[i].timestamp).total_seconds()
                    status_duration[machine_records[i].status.status_name] += duration

                total_time = sum(status_duration.values())
                productive_time = status_duration.get("PRODUCTION", 0)
                total_planned_time += total_time
                total_actual_runtime += productive_time

                # Get planned schedule items
                planned_items = select(p for p in PlannedScheduleItem if
                                       p.machine == machine and
                                       p.initial_start_time >= start_date and
                                       p.initial_end_time <= end_date)[:]

                planned_quantity = sum(item.total_quantity for item in planned_items)

                machine_kpis.append({
                    "machine_id": machine.id,
                    "machine_name": f"{machine.work_center.code}-{machine.make}",
                    "total_production": completed,
                    "quality_rate": ((completed - rejected) / completed * 100) if completed > 0 else 0,
                    "utilization_rate": (productive_time / total_time * 100) if total_time > 0 else 0,
                    "target_achievement": (completed / planned_quantity * 100) if planned_quantity > 0 else 0,
                    "status_distribution": {
                        status: (duration / total_time * 100) if total_time > 0 else 0
                        for status, duration in status_duration.items()
                    }
                })

            return {
                "period_start": start_date,
                "period_end": end_date,
                "overall_metrics": {
                    "total_production": total_production,
                    "quality_rate": ((total_production - total_rejected) / total_production * 100)
                    if total_production > 0 else 0,
                    "plant_utilization": (total_actual_runtime / total_planned_time * 100)
                    if total_planned_time > 0 else 0
                },
                "machine_kpis": machine_kpis,
                "top_bottlenecks": sorted(
                    machine_kpis,
                    key=lambda x: x["utilization_rate"]
                )[:5]
            }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error generating KPI dashboard: {str(e)}"
        )


@router.get("/machine-oee-analysis/{machine_id}", response_model=MachineOEEAnalysis)
async def get_machine_oee_analysis(
        machine_id: int,
        start_date: datetime = Query(default=None),
        end_date: datetime = Query(default=None),
        shift: Optional[int] = Query(None, description="Filter by shift number")
):
    """Get OEE analysis for a specific machine"""
    try:
        with db_session:
            # Import the correct model
            from app.models.production import ShiftSummary

            if not start_date:
                start_date = datetime.utcnow() - timedelta(days=30)  # Default to last 30 days
            if not end_date:
                end_date = datetime.utcnow()

            # Get machine details
            machine = Machine.get(id=machine_id)
            if not machine:
                raise HTTPException(status_code=404, detail="Machine not found")

            machine_name = f"{machine.work_center.code}-{machine.make}"

            # Get shift summaries for the period
            query = select(s for s in ShiftSummary
                           if s.machine_id == machine_id
                           and s.timestamp >= start_date
                           and s.timestamp <= end_date)

            if shift is not None:
                query = query.filter(lambda s: s.shift == shift)

            summaries = query[:]

            if not summaries:
                # Return default values if no data
                return MachineOEEAnalysis(
                    machine_id=machine_id,
                    machine_name=machine_name,
                    average_oee=0.0,
                    average_availability=0.0,
                    average_performance=0.0,
                    average_quality=0.0,
                    oee_trends=[],
                    losses=OEELosses(
                        availability_loss=0.0,
                        performance_loss=0.0,
                        quality_loss=0.0
                    )
                )

            # Calculate averages
            count = len(summaries)
            total_oee = sum(float(s.oee or 0) for s in summaries)
            total_availability = sum(float(s.availability or 0) for s in summaries)
            total_performance = sum(float(s.performance or 0) for s in summaries)
            total_quality = sum(float(s.quality or 0) for s in summaries)

            # Calculate losses
            avg_availability_loss = sum(float(s.availability_loss or 0) for s in summaries) / count if count > 0 else 0
            avg_performance_loss = sum(float(s.performance_loss or 0) for s in summaries) / count if count > 0 else 0
            avg_quality_loss = sum(float(s.quality_loss or 0) for s in summaries) / count if count > 0 else 0

            # Create OEE trends
            oee_trends = []
            for summary in summaries:
                oee_trends.append(OEETrend(
                    date=summary.timestamp.date(),
                    availability=float(summary.availability or 0),
                    performance=float(summary.performance or 0),
                    quality=float(summary.quality or 0),
                    oee=float(summary.oee or 0)
                ))

            return MachineOEEAnalysis(
                machine_id=machine_id,
                machine_name=machine_name,
                average_oee=total_oee / count if count > 0 else 0.0,
                average_availability=total_availability / count if count > 0 else 0.0,
                average_performance=total_performance / count if count > 0 else 0.0,
                average_quality=total_quality / count if count > 0 else 0.0,
                oee_trends=oee_trends,
                losses=OEELosses(
                    availability_loss=avg_availability_loss,
                    performance_loss=avg_performance_loss,
                    quality_loss=avg_quality_loss
                )
            )

    except Exception as e:
        print(f"Error in get_machine_oee_analysis: {str(e)}")
        import traceback
        print(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"Error getting machine OEE analysis: {str(e)}"
        )


@router.get("/detailed-shift-summary/", response_model=List[DetailedShiftSummary])
async def get_detailed_shift_summary(
        start_date: datetime = Query(default=None),
        end_date: datetime = Query(default=None),
        shift: Optional[int] = Query(None, description="Filter by shift number"),
        machine_id: Optional[int] = Query(None, description="Filter by machine ID")
):
    """Get detailed shift summary with OEE metrics and loss analysis for all machines"""
    try:
        with db_session:
            # Import the correct models
            from app.models.production import ShiftSummary, ShiftInfo

            # If no dates provided, use current date
            current_datetime = datetime.utcnow()
            if not start_date:
                start_date = current_datetime.replace(hour=0, minute=0, second=0, microsecond=0)
            if not end_date:
                end_date = current_datetime

            # Get current shift if not specified
            if not shift:
                # Get all shift timings
                shifts = select(s for s in ShiftInfo).order_by(ShiftInfo.start_time)[:]
                current_time = current_datetime.time()

                # Find current shift
                current_shift = None
                for i, shift_info in enumerate(shifts, 1):
                    shift_start = shift_info.start_time
                    shift_end = shift_info.end_time

                    # Handle shifts that cross midnight
                    if shift_end < shift_start:
                        # If current time is after start or before end, it's this shift
                        if current_time >= shift_start or current_time < shift_end:
                            current_shift = i
                            break
                    else:
                        # Normal shift within same day
                        if shift_start <= current_time < shift_end:
                            current_shift = i
                            break

                shift = current_shift if current_shift else 1

            # Get all machines if no specific machine_id provided
            machines = select(m for m in Machine)[:] if machine_id is None else [Machine.get(id=machine_id)]

            summaries = []
            for machine in machines:
                # Get shift summary for this machine
                query = select(s for s in ShiftSummary
                               if s.timestamp >= start_date
                               and s.timestamp <= end_date
                               and s.machine_id == machine.id
                               and s.shift == shift)

                machine_summary = query.first()

                # If no summary exists, create default values
                if not machine_summary:
                    summaries.append(DetailedShiftSummary(
                        date=start_date.date(),
                        shift=shift,
                        machine_id=machine.id,
                        machine_name=f"{machine.work_center.code}-{machine.make}",
                        production_time="00:00:00",
                        idle_time="00:00:00",
                        off_time="00:00:00",
                        total_parts=0,
                        good_parts=0,
                        bad_parts=0,
                        oee_metrics=OEEMetrics(
                            availability=0.0,
                            performance=0.0,
                            quality=0.0,
                            oee=0.0
                        ),
                        loss_analysis=LossAnalysis(
                            availability_loss=0.0,
                            performance_loss=0.0,
                            quality_loss=0.0
                        )
                    ))
                else:
                    summaries.append(DetailedShiftSummary(
                        date=machine_summary.timestamp.date(),
                        shift=machine_summary.shift,
                        machine_id=machine_summary.machine_id,
                        machine_name=f"{machine.work_center.code}-{machine.make}",
                        production_time=str(
                            machine_summary.production_time) if machine_summary.production_time else "00:00:00",
                        idle_time=str(machine_summary.idle_time) if machine_summary.idle_time else "00:00:00",
                        off_time=str(machine_summary.off_time) if machine_summary.off_time else "00:00:00",
                        total_parts=machine_summary.total_parts or 0,
                        good_parts=machine_summary.good_parts or 0,
                        bad_parts=machine_summary.bad_parts or 0,
                        oee_metrics=OEEMetrics(
                            availability=float(machine_summary.availability or 0),
                            performance=float(machine_summary.performance or 0),
                            quality=float(machine_summary.quality or 0),
                            oee=float(machine_summary.oee or 0)
                        ),
                        loss_analysis=LossAnalysis(
                            availability_loss=float(machine_summary.availability_loss or 0),
                            performance_loss=float(machine_summary.performance_loss or 0),
                            quality_loss=float(machine_summary.quality_loss or 0)
                        )
                    ))

            return summaries

    except Exception as e:
        print(f"Error in get_detailed_shift_summary: {str(e)}")
        import traceback
        print(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"Error getting shift summary: {str(e)}"
        )


@router.get("/machine-status-timeline/{machine_id}")
async def get_machine_status_timeline(
        machine_id: int,
        start_date: datetime = Query(default=None),
        end_date: datetime = Query(default=None)
):
    """
    Get machine status changes and distribution over a time range.
    If no dates are provided, shows the last month's data.

    Returns:
    - List of status changes with timestamps
    - Status distribution percentages
    """
    try:
        with db_session:
            if not start_date:
                start_date = datetime.utcnow() - timedelta(days=30)  # Last 30 days
            if not end_date:
                end_date = datetime.utcnow()

            # Get machine status records for the time period
            records = select(r for r in MachineRaw
                             if r.machine_id == machine_id
                             and r.timestamp >= start_date
                             and r.timestamp <= end_date
                             ).order_by(lambda r: r.timestamp)[:]

            # Track status changes
            status_changes = []
            status_duration = defaultdict(int)

            for i, record in enumerate(records):
                # Add status change
                status_changes.append({
                    "timestamp": record.timestamp.isoformat(),
                    "status": record.status.status_name,
                    "program": record.active_program
                })

                # Calculate duration for status distribution
                if i < len(records) - 1:
                    duration = (records[i + 1].timestamp - record.timestamp).total_seconds()
                    status_duration[record.status.status_name] += duration

            # Calculate status distribution percentages
            total_duration = sum(status_duration.values())
            status_distribution = {}

            if total_duration > 0:
                for status, duration in status_duration.items():
                    percentage = (duration / total_duration) * 100
                    status_distribution[status] = round(percentage, 2)

            return {
                "machine_id": machine_id,
                "start_date": start_date,
                "end_date": end_date,
                "status_changes": status_changes,
                "status_distribution": status_distribution
            }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching machine status timeline: {str(e)}"
        )


@router.get("/daily-production-comparison/", response_model=DailyProductionComparison)
async def get_daily_production_comparison(
        date: date = Query(default=None, description="Date to get production comparison for")
):
    """
    Get comparison between planned and actual production for a specific date.
    If no date is provided, returns data for today.
    """
    try:
        with db_session:
            if not date:
                date = datetime.utcnow().date()

            # Convert date to datetime range for the entire day
            start_datetime = datetime.combine(date, datetime.min.time())
            end_datetime = datetime.combine(date, datetime.max.time())

            # Get actual production from production logs
            actual_production = select(
                sum(log.quantity_completed)
                for log in ProductionLog
                if log.start_time >= start_datetime
                and log.end_time <= end_datetime
                and log.quantity_completed > 1  # Exclude setup operations
            ).first() or 0

            # Get planned production from schedule items
            planned_production = select(
                sum(item.total_quantity)
                for item in PlannedScheduleItem
                if item.initial_start_time.date() == date
            ).first() or 0

            # Calculate achievement percentage
            achievement_percentage = (actual_production / planned_production * 100) if planned_production > 0 else 0

            return {
                "date": date,
                "planned_production": planned_production,
                "actual_production": actual_production,
                "achievement_percentage": round(achievement_percentage, 2)
            }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting daily production comparison: {str(e)}"
        )


@router.get("/daily-production-range/", response_model=ProductionDateRange)
async def get_daily_production_range(
        start_date: date = Query(..., description="Start date for production comparison"),
        end_date: date = Query(..., description="End date for production comparison")
):
    """
    Get daily production comparison for all machines within a date range.
    Shows planned vs actual production for each day, with totals.
    """
    try:
        with db_session:
            current_date = start_date
            daily_data = []
            total_planned = 0
            total_actual = 0

            while current_date <= end_date:
                # Convert date to datetime range for the day
                day_start = datetime.combine(current_date, datetime.min.time())
                day_end = datetime.combine(current_date, datetime.max.time())

                # Get all machines
                machines = select(m for m in Machine)[:]
                machine_data = []
                day_planned = 0
                day_actual = 0

                for machine in machines:
                    # Get planned production from schedule items for this machine
                    planned = select(
                        sum(item.total_quantity)
                        for item in PlannedScheduleItem
                        if item.machine.id == machine.id
                        and item.initial_start_time.date() == current_date
                    ).first() or 0

                    # Get actual production from logs for this machine
                    actual = select(
                        sum(log.quantity_completed)
                        for log in ProductionLog
                        if log.machine_id == machine.id
                        and log.start_time >= day_start
                        and log.end_time <= day_end
                        and log.quantity_completed > 1  # Exclude setup operations
                    ).first() or 0

                    achievement = (actual / planned * 100) if planned > 0 else 0

                    machine_data.append(DailyMachineProduction(
                        machine_id=machine.id,
                        machine_name=f"{machine.work_center.code}-{machine.make}",
                        planned_production=planned,
                        actual_production=actual,
                        achievement_percentage=round(achievement, 2)
                    ))

                    day_planned += planned
                    day_actual += actual

                # Calculate daily totals
                day_achievement = (day_actual / day_planned * 100) if day_planned > 0 else 0

                daily_data.append(DailyProductionData(
                    date=current_date,
                    planned_total=day_planned,
                    actual_total=day_actual,
                    achievement_percentage=round(day_achievement, 2),
                    machine_breakdown=machine_data
                ))

                total_planned += day_planned
                total_actual += day_actual
                current_date += timedelta(days=1)

            # Calculate overall achievement
            overall_achievement = (total_actual / total_planned * 100) if total_planned > 0 else 0

            return ProductionDateRange(
                start_date=start_date,
                end_date=end_date,
                daily_production=daily_data,
                total_planned=total_planned,
                total_actual=total_actual,
                overall_achievement=round(overall_achievement, 2)
            )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting production comparison: {str(e)}"
        )


@router.get("/machine-daily-production/{machine_id}", response_model=List[DailyProductionData])
async def get_machine_daily_production(
        machine_id: int,
        start_date: date = Query(..., description="Start date for production comparison"),
        end_date: date = Query(..., description="End date for production comparison")
):
    """
    Get daily production comparison for a specific machine within a date range.
    Shows planned vs actual production for each day.
    """
    try:
        with db_session:
            machine = Machine.get(id=machine_id)
            if not machine:
                raise HTTPException(status_code=404, detail="Machine not found")

            daily_data = []
            current_date = start_date

            while current_date <= end_date:
                # Convert date to datetime range for the day
                day_start = datetime.combine(current_date, datetime.min.time())
                day_end = datetime.combine(current_date, datetime.max.time())

                # Get planned production from schedule items
                planned = select(
                    sum(item.total_quantity)
                    for item in PlannedScheduleItem
                    if item.machine.id == machine_id
                    and item.initial_start_time.date() == current_date
                ).first() or 0

                # Get actual production from logs
                actual = select(
                    sum(log.quantity_completed)
                    for log in ProductionLog
                    if log.machine_id == machine_id
                    and log.start_time >= day_start
                    and log.end_time <= day_end
                    and log.quantity_completed > 1  # Exclude setup operations
                ).first() or 0

                achievement = (actual / planned * 100) if planned > 0 else 0

                daily_data.append(DailyProductionData(
                    date=current_date,
                    planned_total=planned,
                    actual_total=actual,
                    achievement_percentage=round(achievement, 2),
                    machine_breakdown=[
                        DailyMachineProduction(
                            machine_id=machine_id,
                            machine_name=f"{machine.work_center.code}-{machine.make}",
                            planned_production=planned,
                            actual_production=actual,
                            achievement_percentage=round(achievement, 2)
                        )
                    ]
                ))

                current_date += timedelta(days=1)

            return daily_data

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting machine production data: {str(e)}"
        )


@router.get("/all-machines-status-timeline/")
async def get_all_machines_status_timeline(
        start_date: datetime = Query(default=None),
        end_date: datetime = Query(default=None)
):
    """
    Get status timeline data for all machines in a format suitable for timeline visualization.
    If no dates are provided, shows the last 30 days data.

    Returns:
    - time_range: start and end time of the data
    - machines: list of machine names for Y-axis
    - timeline_data: list of status periods with machine_id, start_time, end_time, and status
    """
    try:
        with db_session:
            # Import the correct model
            from app.models.production import MachineRaw

            if not start_date:
                start_date = datetime.utcnow() - timedelta(days=30)
            if not end_date:
                end_date = datetime.utcnow()

            print(f"\n=== Debug: Fetching status timeline for all machines ===")
            print(f"Time range: {start_date} to {end_date}")

            # Get all machines
            machines = select(m for m in Machine)[:]
            print(f"Found {len(machines)} total machines")

            # Initialize response structure
            response = {
                "time_range": {
                    "start": start_date,
                    "end": end_date
                },
                "machines": [{"id": m.id, "name": f"{m.work_center.code}-{m.make}"} for m in machines],
                "timeline_data": []
            }

            # Process each machine's status changes
            for machine in machines:
                try:
                    # Get all records for this machine in chronological order
                    records = select(r for r in MachineRaw
                                     if r.machine_id == machine.id
                                     and r.timestamp >= start_date
                                     and r.timestamp <= end_date
                                     ).order_by(MachineRaw.timestamp)[:]

                    print(f"Found {len(records)} records for machine {machine.id}")

                    if not records:
                        # Add a default "UNKNOWN" status for machines with no data
                        response["timeline_data"].append({
                            "machine_id": machine.id,
                            "machine_name": f"{machine.work_center.code}-{machine.make}",
                            "start_time": start_date,
                            "end_time": end_date,
                            "status": "UNKNOWN",
                            "program": None
                        })
                        continue

                    current_status = None
                    status_start = None

                    for i, record in enumerate(records):
                        if current_status != record.status.status_name:
                            # If there was a previous status, add it to timeline_data
                            if current_status and status_start:
                                response["timeline_data"].append({
                                    "machine_id": machine.id,
                                    "machine_name": f"{machine.work_center.code}-{machine.make}",
                                    "start_time": status_start,
                                    "end_time": record.timestamp,
                                    "status": current_status,
                                    "program": record.program_number if hasattr(record, 'program_number') else None
                                })

                            # Start new status period
                            current_status = record.status.status_name
                            status_start = record.timestamp

                        # Handle the last record
                        if i == len(records) - 1:
                            response["timeline_data"].append({
                                "machine_id": machine.id,
                                "machine_name": f"{machine.work_center.code}-{machine.make}",
                                "start_time": status_start,
                                "end_time": end_date,
                                "status": current_status,
                                "program": record.program_number if hasattr(record, 'program_number') else None
                            })

                except Exception as machine_error:
                    print(f"Error processing machine {machine.id}: {str(machine_error)}")
                    import traceback
                    print(traceback.format_exc())
                    continue

            # Sort timeline_data by start_time
            response["timeline_data"].sort(key=lambda x: x["start_time"])

            return response

    except Exception as e:
        print(f"Error in get_all_machines_status_timeline: {str(e)}")
        import traceback
        print(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching machine status timeline: {str(e)}"
        )


@router.get("/overall-oee-analytics/", response_model=OverallOEEAnalysis)
async def get_overall_oee_analytics(
        start_date: datetime = Query(default=None),
        end_date: datetime = Query(default=None),
        shift: Optional[int] = Query(None, description="Filter by shift number")
):
    """
    Get overall OEE analytics for the entire factory across all machines.
    If no dates are provided, shows the last day's data.
    If no shift is provided, calculates for all shifts.

    Returns:
    - Overall OEE, availability, performance, and quality metrics
    - Daily trends
    - Loss analysis
    - Production totals
    """
    try:
        with db_session:
            # Import the correct models
            from app.models.production import ShiftSummary

            # Default to previous day to current day if no dates provided
            if not start_date:
                start_date = (datetime.utcnow() - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            if not end_date:
                end_date = datetime.utcnow()

            print(f"\n=== Debug: Calculating overall OEE for period {start_date} to {end_date} ===")

            # Build query for shift summaries
            query = select(s for s in ShiftSummary
                           if s.timestamp >= start_date
                           and s.timestamp <= end_date)

            if shift is not None:
                query = query.filter(lambda s: s.shift == shift)
                print(f"Filtering for shift {shift}")

            summaries = query[:]

            if not summaries:
                # Return default values if no data
                return OverallOEEAnalysis(
                    period_start=start_date,
                    period_end=end_date,
                    overall_oee=0.0,
                    overall_availability=0.0,
                    overall_performance=0.0,
                    overall_quality=0.0,
                    shift_breakdown=[],
                    daily_trends=[],
                    losses=OEELosses(
                        availability_loss=0.0,
                        performance_loss=0.0,
                        quality_loss=0.0
                    ),
                    total_production=0,
                    total_good_parts=0,
                    total_bad_parts=0,
                    machine_count=0
                )

            print(f"Found {len(summaries)} shift summary records")

            # Calculate overall metrics
            total_oee = sum(float(s.oee or 0) for s in summaries)
            total_availability = sum(float(s.availability or 0) for s in summaries)
            total_performance = sum(float(s.performance or 0) for s in summaries)
            total_quality = sum(float(s.quality or 0) for s in summaries)

            total_availability_loss = sum(float(s.availability_loss or 0) for s in summaries)
            total_performance_loss = sum(float(s.performance_loss or 0) for s in summaries)
            total_quality_loss = sum(float(s.quality_loss or 0) for s in summaries)

            total_parts = sum(s.total_parts or 0 for s in summaries)
            total_good_parts = sum(s.good_parts or 0 for s in summaries)
            total_bad_parts = sum(s.bad_parts or 0 for s in summaries)

            # Count unique machines
            unique_machines = set(s.machine_id for s in summaries)
            machine_count = len(unique_machines)

            # Calculate averages
            record_count = len(summaries)
            avg_oee = total_oee / record_count if record_count > 0 else 0
            avg_availability = total_availability / record_count if record_count > 0 else 0
            avg_performance = total_performance / record_count if record_count > 0 else 0
            avg_quality = total_quality / record_count if record_count > 0 else 0

            avg_availability_loss = total_availability_loss / record_count if record_count > 0 else 0
            avg_performance_loss = total_performance_loss / record_count if record_count > 0 else 0
            avg_quality_loss = total_quality_loss / record_count if record_count > 0 else 0

            # Create shift breakdown if no specific shift was requested
            shift_breakdown = []
            if shift is None:
                # Group by shift
                shift_data = {}
                for s in summaries:
                    if s.shift not in shift_data:
                        shift_data[s.shift] = {
                            "shift": s.shift,
                            "total_oee": 0,
                            "total_availability": 0,
                            "total_performance": 0,
                            "total_quality": 0,
                            "count": 0,
                            "total_parts": 0,
                            "good_parts": 0,
                            "bad_parts": 0
                        }

                    shift_data[s.shift]["total_oee"] += float(s.oee or 0)
                    shift_data[s.shift]["total_availability"] += float(s.availability or 0)
                    shift_data[s.shift]["total_performance"] += float(s.performance or 0)
                    shift_data[s.shift]["total_quality"] += float(s.quality or 0)
                    shift_data[s.shift]["count"] += 1
                    shift_data[s.shift]["total_parts"] += s.total_parts or 0
                    shift_data[s.shift]["good_parts"] += s.good_parts or 0
                    shift_data[s.shift]["bad_parts"] += s.bad_parts or 0

                # Calculate averages for each shift
                for shift_id, data in shift_data.items():
                    count = data["count"]
                    if count > 0:
                        shift_breakdown.append({
                            "shift": shift_id,
                            "oee": data["total_oee"] / count,
                            "availability": data["total_availability"] / count,
                            "performance": data["total_performance"] / count,
                            "quality": data["total_quality"] / count,
                            "total_parts": data["total_parts"],
                            "good_parts": data["good_parts"],
                            "bad_parts": data["bad_parts"]
                        })

            # Create daily trends
            daily_trends = []
            date_data = {}

            for s in summaries:
                day = s.timestamp.date()
                if day not in date_data:
                    date_data[day] = {
                        "total_oee": 0,
                        "total_availability": 0,
                        "total_performance": 0,
                        "total_quality": 0,
                        "count": 0
                    }

                date_data[day]["total_oee"] += float(s.oee or 0)
                date_data[day]["total_availability"] += float(s.availability or 0)
                date_data[day]["total_performance"] += float(s.performance or 0)
                date_data[day]["total_quality"] += float(s.quality or 0)
                date_data[day]["count"] += 1

            # Calculate daily averages
            for day, data in date_data.items():
                count = data["count"]
                if count > 0:
                    daily_trends.append(OEETrend(
                        date=day,
                        oee=data["total_oee"] / count,
                        availability=data["total_availability"] / count,
                        performance=data["total_performance"] / count,
                        quality=data["total_quality"] / count
                    ))

            # Sort daily trends by date
            daily_trends.sort(key=lambda x: x.date)

            return OverallOEEAnalysis(
                period_start=start_date,
                period_end=end_date,
                overall_oee=avg_oee,
                overall_availability=avg_availability,
                overall_performance=avg_performance,
                overall_quality=avg_quality,
                shift_breakdown=shift_breakdown,
                daily_trends=daily_trends,
                losses=OEELosses(
                    availability_loss=avg_availability_loss,
                    performance_loss=avg_performance_loss,
                    quality_loss=avg_quality_loss
                ),
                total_production=total_parts,
                total_good_parts=total_good_parts,
                total_bad_parts=total_bad_parts,
                machine_count=machine_count
            )

    except Exception as e:
        print(f"Error in get_overall_oee_analytics: {str(e)}")
        import traceback
        print(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"Error calculating overall OEE: {str(e)}"
        )
