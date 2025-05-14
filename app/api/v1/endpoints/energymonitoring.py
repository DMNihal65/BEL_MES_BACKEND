
import asyncio
import json
from asyncio import Task
from asyncio.log import logger

# ''''second code''''''''''''
from fastapi import APIRouter, HTTPException, Query, FastAPI,WebSocket, WebSocketDisconnect
from pony.orm import db_session, select
from typing import List, Optional, Dict, Any

from datetime import datetime, timedelta

from app.database.connection import db
from app.models import Machine
from app.models.energymonitoring import WebSocketManager, MachineEMSLive, ShiftwiseEnergyLive, \
    MachineEMSHistory

import json
import logging
from typing import Dict, Set, Literal
from fastapi import WebSocket

# from app.models.energymonitoring import MachineEMSLive
from app.schemas.energymonitoring import MachineDetailsResponse, EMSDataModel, ShiftwiseEnergyModel

# Create router
# router = APIRouter(prefix="/ems", tags=["EMS"])
router = APIRouter(prefix="/api/v1/energymonitoring", tags=["energymonitoring"])
# '''''''''''WEBSCOKETS EndpointS STARTS''''''''''''''''''''''''

# ws_router = APIRouter(tags=["WebSockets"])
# Create WebSocket manager instance
websocket_manager = WebSocketManager()
#

@router.websocket("/ws/shiftwise_energy_live")
async def websocket_shiftwise_energy_live(websocket: WebSocket):
    """
    WebSocket endpoint for continuously streaming shiftwise energy live data

    Clients connect with initial JSON message:
    {
        "machine_id": 123,
        "column_name": "first_shift" | "second_shift" | "third_shift" | "total_energy"
    }

    Server then continually streams data at regular intervals without requiring
    additional client messages.
    """
    machine_id = None
    column_name = None

    try:
        # Accept the WebSocket connection
        await websocket.accept()

        # Wait for the initial configuration message
        data = await websocket.receive_json()

        # Validate received initial data
        if not isinstance(data, dict) or "machine_id" not in data or "column_name" not in data:
            await websocket.send_json({
                "error": "Invalid request format. Please provide machine_id and column_name."
            })
            return

        try:
            machine_id = int(data.get("machine_id"))
            column_name = str(data.get("column_name"))

            # Initial validation
            with db_session:
                # Check if machine exists
                machine = Machine.get(id=machine_id)
                if not machine:
                    await websocket.send_json({
                        "error": f"Machine with ID {machine_id} not found"
                    })
                    return

                # Validate column name
                valid_columns = ["first_shift", "second_shift", "third_shift", "total_energy"]
                if column_name not in valid_columns:
                    await websocket.send_json({
                        "error": f"Invalid column name. Must be one of: {', '.join(valid_columns)}"
                    })
                    return

            # Enter continuous streaming loop
            while True:
                # Get the latest data with db_session
                with db_session:
                    # Using SQL directly to select only the required columns
                    query = f"""
                    SELECT timestamp, {column_name}, machine_id
                    FROM ems.shiftwise_energy_live
                    WHERE machine_id = $machine_id
                    """

                    # Execute raw SQL with parameters
                    live_data = db.execute(query, {'machine_id': machine_id})

                    # Convert raw result to proper dictionary
                    column_names = [desc[0] for desc in live_data.description]
                    result = {}

                    # Process the row
                    row = live_data.fetchone()
                    if row:
                        for i, value in enumerate(row):
                            # Convert datetime objects to string for JSON serialization
                            if isinstance(value, datetime):
                                result[column_names[i]] = value.isoformat()
                            else:
                                result[column_names[i]] = value

                        # Send the result back to the client
                        await websocket.send_json(result)
                    else:
                        await websocket.send_json({
                            "error": f"No live shiftwise energy data available for Machine ID {machine_id}"
                        })

                # Wait before sending the next update (adjust the interval as needed)
                await asyncio.sleep(1)  # Send data every 1 second

        except ValueError as e:
            await websocket.send_json({
                "error": f"Invalid parameters: {str(e)}"
            })
        except Exception as e:
            logging.error(f"Error processing WebSocket request: {str(e)}")
            await websocket.send_json({
                "error": f"Internal server error occurred: {str(e)}"
            })

    except WebSocketDisconnect:
        logging.info(f"WebSocket client disconnected - machine_id: {machine_id}, column: {column_name}")
    except Exception as e:
        logging.error(f"WebSocket error: {str(e)}")
    finally:
        # You could add cleanup code here if needed
        logging.info(f"WebSocket connection closed - machine_id: {machine_id}, column: {column_name}")


@router.websocket("/ws/shiftwise_energy_live_complete")
async def websocket_shiftwise_energy_live_complete(websocket: WebSocket):
    """
    WebSocket endpoint for continuously streaming complete shiftwise energy live data

    Clients connect with initial JSON message:
    {
        "machine_id": 123
    }

    Server then continually streams all ShiftwiseEnergyLive data fields
    at regular intervals without requiring additional client messages.
    """
    machine_id = None

    try:
        # Accept the WebSocket connection
        await websocket.accept()

        # Wait for the initial configuration message
        data = await websocket.receive_json()

        # Validate received initial data
        if not isinstance(data, dict) or "machine_id" not in data:
            await websocket.send_json({
                "error": "Invalid request format. Please provide machine_id."
            })
            return

        try:
            machine_id = int(data.get("machine_id"))

            # Initial validation
            with db_session:
                # Check if machine exists
                machine = Machine.get(id=machine_id)
                if not machine:
                    await websocket.send_json({
                        "error": f"Machine with ID {machine_id} not found"
                    })
                    return

            # Enter continuous streaming loop
            while True:
                # Get the latest data with db_session
                with db_session:
                    # Query for the live data
                    live_data = ShiftwiseEnergyLive.get(machine_id=machine_id)

                    if not live_data:
                        await websocket.send_json({
                            "error": f"No live shiftwise energy data available for Machine ID {machine_id}"
                        })
                    else:
                        # Convert Pony ORM entity to dictionary
                        result = {
                            "machine_id": live_data.machine_id,
                            "timestamp": live_data.timestamp.isoformat() if live_data.timestamp else None,
                            "first_shift": live_data.first_shift,
                            "second_shift": live_data.second_shift,
                            "third_shift": live_data.third_shift,
                            "total_energy": live_data.total_energy
                        }

                        # Send the result back to the client
                        await websocket.send_json(result)

                # Wait before sending the next update
                await asyncio.sleep(1)  # Send data every 1 second

        except ValueError as e:
            await websocket.send_json({
                "error": f"Invalid parameters: {str(e)}"
            })
        except Exception as e:
            logging.error(f"Error processing WebSocket request: {str(e)}")
            await websocket.send_json({
                "error": f"Internal server error occurred: {str(e)}"
            })

    except WebSocketDisconnect:
        logging.info(f"WebSocket client disconnected - machine_id: {machine_id}")
    except Exception as e:
        logging.error(f"WebSocket error: {str(e)}")
    finally:
        # Cleanup code if needed
        logging.info(f"WebSocket connection closed - machine_id: {machine_id}")


@router.websocket("/ws/shiftwise_energy_history")
async def websocket_shiftwise_energy_history(websocket: WebSocket):
    """
    WebSocket endpoint for streaming shiftwise energy history data

    Clients connect with initial JSON message:
    {
        "machine_id": 123,
        "column_name": "first_shift" | "second_shift" | "third_shift" | "total_energy"
    }

    Server streams the requested history data and then maintains the connection
    to continuously send updates when new history data becomes available.
    """
    machine_id = None
    column_name = None

    try:
        # Accept the WebSocket connection
        await websocket.accept()

        # Wait for the initial configuration message
        data = await websocket.receive_json()

        # Validate received initial data
        if not isinstance(data, dict) or "machine_id" not in data or "column_name" not in data:
            await websocket.send_json({
                "error": "Invalid request format. Please provide machine_id and column_name."
            })
            return

        try:
            machine_id = int(data.get("machine_id"))
            column_name = str(data.get("column_name"))

            # Initial validation
            with db_session:
                # Check if machine exists
                machine = Machine.get(id=machine_id)
                if not machine:
                    await websocket.send_json({
                        "error": f"Machine with ID {machine_id} not found"
                    })
                    return

                # Validate column name
                valid_columns = ["first_shift", "second_shift", "third_shift", "total_energy"]
                if column_name not in valid_columns:
                    await websocket.send_json({
                        "error": f"Invalid column name. Must be one of: {', '.join(valid_columns)}"
                    })
                    return

            # Send initial notification
            await websocket.send_json({
                "type": "notification",
                "message": f"Streaming history data for Machine ID {machine_id}, column: {column_name}"
            })

            # Variable to track the latest timestamp we've seen
            latest_timestamp = None

            # Get the initial history data and send it
            with db_session:
                # Using SQL directly to get all historical data for the requested column
                query = f"""
                SELECT timestamp, {column_name}, machine_id 
                FROM ems.shiftwise_energy_history 
                WHERE machine_id = $machine_id 
                ORDER BY timestamp ASC
                """

                # Execute raw SQL with parameters
                history_data = db.execute(query, {'machine_id': machine_id})

                # Convert raw result to proper dictionaries
                column_names = [desc[0] for desc in history_data.description]
                result = []

                for row in history_data:
                    record = {}
                    for i, value in enumerate(row):
                        # Convert datetime objects to string for JSON serialization
                        if isinstance(value, datetime):
                            if i == 0:  # Assuming timestamp is always the first column
                                # Update the latest timestamp if this is newer
                                if latest_timestamp is None or value > latest_timestamp:
                                    latest_timestamp = value
                            record[column_names[i]] = value.isoformat()
                        else:
                            record[column_names[i]] = value
                    result.append(record)

                # Send initial batch of historical data
                if result:
                    await websocket.send_json({
                        "type": "history_data",
                        "data": result
                    })
                else:
                    await websocket.send_json({
                        "type": "notification",
                        "message": f"No shiftwise energy history data available for Machine ID {machine_id}"
                    })

            # Enter continuous streaming loop to check for new data
            while True:
                # Wait before checking for updates
                await asyncio.sleep(1)  # Reduced from 5 seconds to 1 second to match live endpoint

                # Always send the latest data, similar to the live endpoint
                with db_session:
                    # Using SQL directly to get the latest data
                    query = f"""
                    SELECT timestamp, {column_name}, machine_id 
                    FROM ems.shiftwise_energy_history 
                    WHERE machine_id = $machine_id 
                    ORDER BY timestamp DESC
                    LIMIT 10
                    """

                    # Execute raw SQL with parameters
                    latest_data = db.execute(query, {
                        'machine_id': machine_id
                    })

                    # Convert raw result to proper dictionaries
                    column_names = [desc[0] for desc in latest_data.description]
                    result = []

                    for row in latest_data:
                        record = {}
                        for i, value in enumerate(row):
                            # Convert datetime objects to string for JSON serialization
                            if isinstance(value, datetime):
                                if i == 0:  # Assuming timestamp is always the first column
                                    # Track the latest timestamp
                                    if latest_timestamp is None or value > latest_timestamp:
                                        latest_timestamp = value
                                record[column_names[i]] = value.isoformat()
                            else:
                                record[column_names[i]] = value
                        result.append(record)

                    # Always send data at regular intervals like the live endpoint
                    await websocket.send_json({
                        "type": "history_data",
                        "data": result,
                        "timestamp": datetime.now().isoformat(),
                        "is_update": True
                    })

        except ValueError as e:
            await websocket.send_json({
                "error": f"Invalid parameters: {str(e)}"
            })
        except Exception as e:
            logging.error(f"Error processing WebSocket request: {str(e)}")
            await websocket.send_json({
                "error": f"Internal server error occurred: {str(e)}"
            })

    except WebSocketDisconnect:
        logging.info(f"WebSocket client disconnected - machine_id: {machine_id}, column: {column_name}")
    except Exception as e:
        logging.error(f"WebSocket error: {str(e)}")
    finally:
        # Cleanup code if needed
        logging.info(f"WebSocket connection closed - machine_id: {machine_id}, column: {column_name}")


@router.websocket("/ws/filtered_history_data")
async def websocket_filtered_history_data(websocket: WebSocket):
    """
    WebSocket endpoint for streaming filtered machine history data

    Clients connect with initial JSON message:
    {
        "machine_id": 123,
        "start_date": "YYYY-MM-DD",
        "end_date": "YYYY-MM-DD",
        "column_name": "phase_a_voltage" (or any valid column)
    }

    Server streams the requested filtered history data and then maintains the connection
    to continuously send updates when new data becomes available within the date range.
    """
    machine_id = None
    column_name = None
    start_datetime = None
    end_datetime = None

    try:
        # Accept the WebSocket connection
        await websocket.accept()

        # Wait for the initial configuration message
        data = await websocket.receive_json()

        # Validate received initial data
        required_fields = ["machine_id", "start_date", "end_date", "column_name"]
        if not isinstance(data, dict) or not all(field in data for field in required_fields):
            await websocket.send_json({
                "error": f"Invalid request format. Please provide: {', '.join(required_fields)}"
            })
            return

        try:
            machine_id = int(data.get("machine_id"))
            column_name = str(data.get("column_name"))
            start_date = str(data.get("start_date")).strip()
            end_date = str(data.get("end_date")).strip()

            # Parse and validate dates
            try:
                # Parse the dates
                start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
                end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()

                # Convert dates to datetime for comparison
                # Set time to 00:00:00 for start_date and 23:59:59 for end_date to include the entire day
                start_datetime = datetime.combine(start_date_obj, datetime.min.time())
                end_datetime = datetime.combine(end_date_obj, datetime.max.time())

                # Validate date range
                if end_date_obj < start_date_obj:
                    await websocket.send_json({
                        "error": "End date cannot be before start date"
                    })
                    return
            except ValueError:
                await websocket.send_json({
                    "error": "Invalid date format. Use YYYY-MM-DD format."
                })
                return

            # Initial validation
            with db_session:
                # Check if machine exists
                machine = Machine.get(id=machine_id)
                if not machine:
                    await websocket.send_json({
                        "error": f"Machine with ID {machine_id} not found"
                    })
                    return

                # Validate requested column
                available_columns = [
                    "id", "machine_id", "timestamp",
                    "phase_a_voltage", "phase_b_voltage", "phase_c_voltage",
                    "avg_phase_voltage", "line_ab_voltage", "line_bc_voltage",
                    "line_ca_voltage", "avg_line_voltage",
                    "phase_a_current", "phase_b_current", "phase_c_current",
                    "avg_three_phase_current", "power_factor", "frequency",
                    "total_instantaneous_power", "active_energy_delivered"
                ]

                if column_name not in available_columns:
                    await websocket.send_json({
                        "error": f"Invalid column: '{column_name}'. Available columns: {', '.join(available_columns)}"
                    })
                    return

            # Send initial notification
            await websocket.send_json({
                "type": "notification",
                "message": f"Streaming filtered history data for Machine ID {machine_id}, column: {column_name}, date range: {start_date} to {end_date}"
            })

            # Always include these essential columns plus the requested column
            select_columns = "id, machine_id, timestamp, " + column_name
            if column_name in ["id", "machine_id", "timestamp"]:
                select_columns = "id, machine_id, timestamp"

            # Get the initial history data and send it
            with db_session:
                # Using SQL directly with selected columns
                query = f"""
                SELECT {select_columns} FROM ems.machine_ems_history 
                WHERE machine_id = $machine_id 
                AND timestamp >= $start_datetime 
                AND timestamp <= $end_datetime 
                ORDER BY timestamp ASC
                """

                # Execute raw SQL with parameters
                history_data = db.execute(query, {
                    'machine_id': machine_id,
                    'start_datetime': start_datetime,
                    'end_datetime': end_datetime
                })

                # Convert raw result to proper dictionaries
                column_names = [desc[0] for desc in history_data.description]
                result = []

                for row in history_data:
                    record = {}
                    for i, value in enumerate(row):
                        # Convert datetime objects to string for JSON serialization
                        if isinstance(value, datetime):
                            record[column_names[i]] = value.isoformat()
                        else:
                            record[column_names[i]] = value
                    result.append(record)

                # Send initial batch of historical data
                if result:
                    await websocket.send_json({
                        "type": "history_data",
                        "data": result
                    })
                else:
                    await websocket.send_json({
                        "type": "notification",
                        "message": f"No history data available for Machine ID {machine_id} in the specified date range"
                    })

            # Variable to track the latest timestamp we've seen
            latest_timestamp = None
            if result and len(result) > 0:
                for record in result:
                    timestamp_str = record.get("timestamp")
                    if timestamp_str:
                        timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                        if latest_timestamp is None or timestamp > latest_timestamp:
                            latest_timestamp = timestamp

            # Enter continuous streaming loop to check for new data
            while True:
                # Wait before checking for updates
                await asyncio.sleep(1)  # Check for new history data every second

                # Always send the latest data, similar to the live endpoint
                with db_session:
                    # Get the latest data within the date range
                    query = f"""
                    SELECT {select_columns} FROM ems.machine_ems_history 
                    WHERE machine_id = $machine_id 
                    AND timestamp >= $start_datetime 
                    AND timestamp <= $end_datetime 
                    ORDER BY timestamp DESC
                    LIMIT 20
                    """

                    # Execute raw SQL with parameters
                    latest_data = db.execute(query, {
                        'machine_id': machine_id,
                        'start_datetime': start_datetime,
                        'end_datetime': end_datetime
                    })

                    # Convert raw result to proper dictionaries
                    column_names = [desc[0] for desc in latest_data.description]
                    result = []

                    for row in latest_data:
                        record = {}
                        for i, value in enumerate(row):
                            # Convert datetime objects to string for JSON serialization
                            if isinstance(value, datetime):
                                record[column_names[i]] = value.isoformat()
                            else:
                                record[column_names[i]] = value
                        result.append(record)

                    # Always send data at regular intervals like the live endpoint
                    await websocket.send_json({
                        "type": "history_data",
                        "data": result,
                        "timestamp": datetime.now().isoformat(),
                        "is_update": True
                    })

        except ValueError as e:
            await websocket.send_json({
                "error": f"Invalid parameters: {str(e)}"
            })
        except Exception as e:
            logging.error(f"Error processing WebSocket request: {str(e)}")
            await websocket.send_json({
                "error": f"Internal server error occurred: {str(e)}"
            })

    except WebSocketDisconnect:
        logging.info(
            f"WebSocket client disconnected - machine_id: {machine_id}, column: {column_name}, date range: {start_datetime} to {end_datetime}")
    except Exception as e:
        logging.error(f"WebSocket error: {str(e)}")
    finally:
        # Cleanup code if needed
        logging.info(
            f"WebSocket connection closed - machine_id: {machine_id}, column: {column_name}, date range: {start_datetime} to {end_datetime}")


@router.websocket("/ws/filtered_live_data")
async def websocket_filtered_live_data(websocket: WebSocket):
    """
    WebSocket endpoint for streaming filtered machine live data

    Clients connect with initial JSON message:
    {
        "machine_id": 123,
        "column_name": "phase_a_voltage", (or any valid column)
        "datetime_filter": "YYYY-MM-DD HH:MM:SS" (optional)
    }

    Server streams the requested filtered live data and then maintains the connection
    to continuously send updates when new data becomes available.
    """
    machine_id = None
    column_name = None
    datetime_filter = None

    try:
        # Accept the WebSocket connection
        await websocket.accept()

        # Wait for the initial configuration message
        data = await websocket.receive_json()

        # Validate received initial data
        required_fields = ["machine_id", "column_name"]
        if not isinstance(data, dict) or not all(field in data for field in required_fields):
            await websocket.send_json({
                "error": f"Invalid request format. Please provide: {', '.join(required_fields)}"
            })
            return

        try:
            machine_id = int(data.get("machine_id"))
            column_name = str(data.get("column_name"))
            datetime_filter = data.get("datetime_filter")

            # Initial validation
            with db_session:
                # Check if machine exists
                machine = Machine.get(id=machine_id)
                if not machine:
                    await websocket.send_json({
                        "error": f"Machine with ID {machine_id} not found"
                    })
                    return

                # Validate requested column
                available_columns = [
                    "id", "machine_id", "timestamp", "status",
                    "phase_a_voltage", "phase_b_voltage", "phase_c_voltage",
                    "avg_phase_voltage", "line_ab_voltage", "line_bc_voltage",
                    "line_ca_voltage", "avg_line_voltage",
                    "phase_a_current", "phase_b_current", "phase_c_current",
                    "avg_three_phase_current", "power_factor", "frequency",
                    "total_instantaneous_power", "active_energy_delivered"
                ]

                if column_name not in available_columns:
                    await websocket.send_json({
                        "error": f"Invalid column: '{column_name}'. Available columns: {', '.join(available_columns)}"
                    })
                    return

            # Send initial notification
            notification_message = f"Streaming filtered live data for Machine ID {machine_id}, column: {column_name}"
            if datetime_filter:
                notification_message += f", datetime filter: {datetime_filter}"

            await websocket.send_json({
                "type": "notification",
                "message": notification_message
            })

            # Always include these essential columns plus the requested column
            select_columns = ["id", "machine_id", "timestamp"]
            if column_name not in select_columns:
                select_columns.append(column_name)

            select_columns_str = ", ".join(select_columns)

            # Process datetime filter if provided
            filter_minute_start = None
            filter_minute_end = None

            if datetime_filter:
                try:
                    # Strip any whitespace
                    datetime_filter = datetime_filter.strip()

                    # Check if we have seconds in the input
                    if len(datetime_filter.split(':')) == 2:
                        # If only HH:MM provided, add ":00" for seconds
                        datetime_filter += ":00"

                    # Parse the datetime
                    filter_datetime = datetime.strptime(datetime_filter, "%Y-%m-%d %H:%M:%S")

                    # Get the minute part for partial matching
                    filter_minute_start = filter_datetime.replace(second=0)
                    filter_minute_end = filter_datetime.replace(second=59)

                except ValueError:
                    await websocket.send_json({
                        "error": "Invalid datetime format. Use YYYY-MM-DD HH:MM:SS or YYYY-MM-DD HH:MM format."
                    })
                    return

            # Enter continuous streaming loop
            while True:
                with db_session:
                    # Build SQL query
                    query = f"""
                    SELECT {select_columns_str} FROM ems.machine_ems_live 
                    WHERE machine_id = $machine_id
                    """

                    params = {'machine_id': machine_id}

                    # Add timestamp filter if provided
                    if datetime_filter and filter_minute_start and filter_minute_end:
                        query += " AND timestamp BETWEEN $minute_start AND $minute_end"
                        params['minute_start'] = filter_minute_start
                        params['minute_end'] = filter_minute_end
                    else:
                        # Get the latest record if no specific timestamp
                        query += " ORDER BY timestamp DESC LIMIT 1"

                    # Execute raw SQL with parameters
                    result_data = db.execute(query, params)

                    # Convert raw result to proper dictionary
                    column_names = [desc[0] for desc in result_data.description]
                    rows = list(result_data)

                    result = []

                    for row in rows:
                        record = {}
                        for i, value in enumerate(row):
                            # Convert datetime objects to string for JSON serialization
                            if isinstance(value, datetime):
                                record[column_names[i]] = value.isoformat()
                            else:
                                record[column_names[i]] = value
                        result.append(record)

                    # Send the data
                    await websocket.send_json({
                        "type": "live_data",
                        "data": result,
                        "timestamp": datetime.now().isoformat()
                    })

                # Wait before checking for updates
                await asyncio.sleep(1)  # Stream live data every second

        except ValueError as e:
            await websocket.send_json({
                "error": f"Invalid parameters: {str(e)}"
            })
        except Exception as e:
            logging.error(f"Error processing WebSocket request: {str(e)}")
            await websocket.send_json({
                "error": f"Internal server error occurred: {str(e)}"
            })

    except WebSocketDisconnect:
        logging.info(
            f"WebSocket client disconnected - machine_id: {machine_id}, column: {column_name}, datetime_filter: {datetime_filter}")
    except Exception as e:
        logging.error(f"WebSocket error: {str(e)}")
    finally:
        # Cleanup code if needed
        logging.info(
            f"WebSocket connection closed - machine_id: {machine_id}, column: {column_name}, datetime_filter: {datetime_filter}")


@router.websocket("/ws/live_data")
async def websocket_live_data(websocket: WebSocket):
    """
    WebSocket endpoint for streaming complete machine live data

    Clients connect with initial JSON message:
    {
        "machine_id": 123
    }

    Server streams the complete live data for the specified machine and maintains
    the connection to continuously send updates when new data becomes available.
    """
    machine_id = None

    try:
        # Accept the WebSocket connection
        await websocket.accept()

        # Wait for the initial configuration message
        data = await websocket.receive_json()

        # Validate received initial data
        if not isinstance(data, dict) or "machine_id" not in data:
            await websocket.send_json({
                "error": "Invalid request format. Please provide machine_id."
            })
            return

        try:
            machine_id = int(data.get("machine_id"))

            # Initial validation
            with db_session:
                # Check if machine exists
                machine = Machine.get(id=machine_id)
                if not machine:
                    await websocket.send_json({
                        "error": f"Machine with ID {machine_id} not found"
                    })
                    return

            # Send initial notification
            await websocket.send_json({
                "type": "notification",
                "message": f"Streaming live data for Machine ID {machine_id}"
            })

            # Enter continuous streaming loop
            while True:
                with db_session:
                    # Query live data using Pony ORM
                    live_data = MachineEMSLive.get(machine_id=machine_id)

                    if live_data:
                        # Convert to dictionary and ensure datetime is serializable
                        result = live_data.to_dict()

                        # Convert datetime objects to ISO format strings for JSON serialization
                        for key, value in result.items():
                            if isinstance(value, datetime):
                                result[key] = value.isoformat()

                        # Send the data
                        await websocket.send_json({
                            "type": "live_data",
                            "data": result,
                            "timestamp": datetime.now().isoformat()
                        })
                    else:
                        # If no data found, send notification
                        await websocket.send_json({
                            "type": "notification",
                            "message": f"No live data available for Machine ID {machine_id}"
                        })

                # Wait before checking for updates
                await asyncio.sleep(1)  # Stream live data every second

        except ValueError as e:
            await websocket.send_json({
                "error": f"Invalid parameters: {str(e)}"
            })
        except Exception as e:
            logging.error(f"Error processing WebSocket request: {str(e)}")
            await websocket.send_json({
                "error": f"Internal server error occurred: {str(e)}"
            })

    except WebSocketDisconnect:
        logging.info(f"WebSocket client disconnected - machine_id: {machine_id}")
    except Exception as e:
        logging.error(f"WebSocket error: {str(e)}")
    finally:
        # Cleanup code if needed
        logging.info(f"WebSocket connection closed - machine_id: {machine_id}")


@router.websocket("/ws/machines_data")
async def websocket_machines_data(websocket: WebSocket):
    """
    WebSocket endpoint for streaming data for all machines with their details and status

    Connection starts streaming automatically without requiring any initial message.
    Server streams data for all machines and maintains the connection
    to continuously send updates when new data becomes available.
    """
    try:
        # Accept the WebSocket connection
        await websocket.accept()

        # Keep track of connection status
        is_connected = True

        # Log connection
        logging.info("WebSocket client connected to machines data stream")

        # Send initial notification
        await websocket.send_json({
            "type": "notification",
            "message": "Connected to machines data stream"
        })

        # Enter continuous streaming loop
        while is_connected:
            try:
                with db_session:
                    # Get all machines
                    machines = list(Machine.select())

                    result = []
                    for machine in machines:
                        machine_data = machine.to_dict()
                        ems_live = MachineEMSLive.get(machine_id=machine.id)

                        # Convert machine data datetime objects to strings
                        for key, value in machine_data.items():
                            if isinstance(value, datetime):
                                machine_data[key] = value.isoformat()

                        response = {
                            "machine_id": machine.id,
                            "machine_data": machine_data,
                            "status": ems_live.status if ems_live else None,
                            "timestamp": ems_live.timestamp.isoformat() if ems_live and ems_live.timestamp else None
                        }
                        result.append(response)

                    # Send the data
                    await websocket.send_json({
                        "type": "machines_data",
                        "data": result,
                        "timestamp": datetime.now().isoformat()
                    })

                # Wait before checking for updates
                await asyncio.sleep(2)  # Stream data every 2 seconds

            except Exception as e:
                logging.error(f"Error in data processing loop: {str(e)}")
                # Send error to client
                await websocket.send_json({
                    "error": f"Data fetch error: {str(e)}"
                })
                await asyncio.sleep(5)  # Wait longer after an error

    except WebSocketDisconnect:
        logging.info("WebSocket client disconnected from machines data stream")
        is_connected = False
    except Exception as e:
        logging.error(f"WebSocket error: {str(e)}")
        is_connected = False
    finally:
        # Cleanup code if needed
        logging.info("WebSocket connection closed for machines data stream")

@router.websocket("/ws/shiftwise_energy")
async def websocket_shiftwise_energy_live(websocket: WebSocket):
    """
    WebSocket endpoint for streaming ShiftwiseEnergyLive data
    """
    try:
        # Accept the WebSocket connection
        await websocket.accept()

        # Keep track of connection status
        is_connected = True

        # Log connection
        logging.info("WebSocket client connected to ShiftwiseEnergyLive stream")

        # Send initial notification
        await websocket.send_json({
            "type": "notification",
            "message": "Connected to ShiftwiseEnergyLive stream"
        })

        # Continue sending data while connected
        while is_connected:
            try:
                # Use db_session for each data fetch
                with db_session:
                    # Query all records - same exact query as your working endpoint
                    all_records = list(ShiftwiseEnergyLive.select())

                    # If records found, send them
                    if all_records:
                        # Convert to serializable format
                        serialized_data = []
                        for record in all_records:
                            # Extract the exact fields you need
                            data_dict = {
                                "timestamp": record.timestamp.isoformat() if hasattr(record.timestamp,
                                                                                     'isoformat') else str(
                                    record.timestamp),
                                "first_shift": record.first_shift,
                                "second_shift": record.second_shift,
                                "third_shift": record.third_shift,
                                "total_energy": record.total_energy,
                                "machine_id": record.machine_id
                            }
                            serialized_data.append(data_dict)

                        # Send data as JSON
                        await websocket.send_json({
                            "data": serialized_data
                        })
                    else:
                        # No data found
                        await websocket.send_json({
                            "data": [],
                            "message": "No records found"
                        })

                # Wait before sending next update
                await asyncio.sleep(1)

            except Exception as e:
                logging.error(f"Error in data processing loop: {str(e)}")
                # Send error to client
                await websocket.send_json({
                    "error": f"Data fetch error: {str(e)}"
                })
                await asyncio.sleep(5)  # Wait longer after an error

    except WebSocketDisconnect:
        logging.info("WebSocket client disconnected")
        is_connected = False
    except Exception as e:
        logging.error(f"WebSocket error: {str(e)}")
        is_connected = False
    finally:
        logging.info("WebSocket connection closed")


@router.websocket("/ws/shiftwise_energy_history_by_date")
async def websocket_shiftwise_energy_history_by_date(websocket: WebSocket):
    """
    WebSocket endpoint for streaming shiftwise energy history data by date

    Clients connect with initial JSON message:
    {
        "start_date": "YYYY-MM-DD"  # Date format without time
    }

    Server continuously streams all records from the start_date onwards,
    sending complete data in each cycle regardless of whether new data exists.
    """
    start_date = None

    try:
        # Accept the WebSocket connection
        await websocket.accept()

        # Wait for the initial configuration message
        data = await websocket.receive_json()

        # Validate received initial data
        if not isinstance(data, dict) or "start_date" not in data:
            await websocket.send_json({
                "error": "Invalid request format. Please provide start_date in format YYYY-MM-DD."
            })
            return

        try:
            # Parse the start date (without time)
            start_date_str = str(data.get("start_date"))
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d")

            # Send initial notification
            await websocket.send_json({
                "type": "notification",
                "message": f"Streaming energy history data from date: {start_date_str}"
            })

            # Enter continuous streaming loop
            while True:
                # Get all historical data from the start date in each cycle
                with db_session:
                    # Using SQL directly to get all historical data from the start date
                    query = """
                    SELECT timestamp, first_shift, second_shift, third_shift, total_energy, machine_id
                    FROM ems.shiftwise_energy_history 
                    WHERE timestamp >= $start_date 
                    ORDER BY timestamp ASC
                    """

                    # Execute raw SQL with parameters
                    history_data = db.execute(query, {'start_date': start_date})

                    # Convert raw result to proper dictionaries
                    column_names = [desc[0] for desc in history_data.description]
                    result = []

                    for row in history_data:
                        record = {}
                        for i, value in enumerate(row):
                            # Convert datetime objects to string for JSON serialization
                            if isinstance(value, datetime):
                                record[column_names[i]] = value.isoformat()
                            else:
                                record[column_names[i]] = value
                        result.append(record)

                    # Always send data in each cycle, regardless of whether it's new
                    await websocket.send_json({
                        "type": "history_data",
                        "data": result,
                        "timestamp": datetime.now().isoformat()
                    })

                # Wait before next cycle
                await asyncio.sleep(1)

        except ValueError as e:
            await websocket.send_json({
                "error": f"Invalid date format. Please use YYYY-MM-DD format: {str(e)}"
            })
        except Exception as e:
            logging.error(f"Error processing WebSocket request: {str(e)}")
            await websocket.send_json({
                "error": f"Internal server error occurred: {str(e)}"
            })

    except WebSocketDisconnect:
        logging.info(f"WebSocket client disconnected - streaming from date: {start_date}")
    except Exception as e:
        logging.error(f"WebSocket error: {str(e)}")
    finally:
        # Cleanup code if needed
        logging.info(f"WebSocket connection closed - streaming from date: {start_date}")

# ''''''''''END OF WEBSCOKETS ''''''''''''''''''''''''''''''''''''


@router.get("/machine/{machine_id}", response_model=MachineDetailsResponse)
@db_session
def get_machine_details(machine_id: int):
    """
    Fetch machine details from master_order.machines along with status from machine_ems_live
    """
    # Query the Machine entity to get machine data
    machine = Machine.get(id=machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail=f"Machine with ID {machine_id} not found")

    # Convert to dictionary with all fields
    machine_data = machine.to_dict()

    # Query MachineEMSLive for status
    ems_live = MachineEMSLive.get(machine_id=machine_id)

    # Prepare response
    response = {
        "machine_id": machine_id,
        "machine_data": machine_data,
        "status": ems_live.status if ems_live else None,
        "timestamp": ems_live.timestamp if ems_live else None
    }

    return response


@router.get("/machines", response_model=List[MachineDetailsResponse])
@db_session
def get_all_machines():
    """
    Fetch all machines with their details and status
    """
    # Fix: Using list() instead of [:]
    machines = list(Machine.select())

    result = []
    for machine in machines:
        machine_data = machine.to_dict()
        ems_live = MachineEMSLive.get(machine_id=machine.id)

        response = {
            "machine_id": machine.id,
            "machine_data": machine_data,
            "status": ems_live.status if ems_live else None,
            "timestamp": ems_live.timestamp if ems_live else None
        }
        result.append(response)

    return result


@router.get("/live/{machine_id}", response_model=EMSDataModel)
@db_session
def get_machine_live_data(machine_id: int):
    """
    Fetch the latest live EMS data for a specific machine

    Parameters:
    - machine_id: ID of the machine

    Returns:
    - Latest EMS live data for the specified machine
    """
    # Check if machine exists
    machine = Machine.get(id=machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail=f"Machine with ID {machine_id} not found")

    # Query live data
    live_data = MachineEMSLive.get(machine_id=machine_id)
    if not live_data:
        raise HTTPException(status_code=404, detail=f"No live data available for Machine ID {machine_id}")

    return live_data.to_dict()


@router.get("/filtered_live/{machine_id}", response_model=Dict)
@db_session
def get_filtered_machine_live_data(
        machine_id: int,
        column_name: str = Query(..., description="Specific column to retrieve (e.g., phase_a_voltage)"),
        datetime_filter: str = Query(None,
                                     description="Filter by specific date and time (YYYY-MM-DD HH:MM:SS or YYYY-MM-DD HH:MM)")
):
    """
    Fetch specific live EMS data column for a machine with optional datetime filtering

    Parameters:
    - machine_id: ID of the machine
    - column_name: Name of the specific column to retrieve (e.g., phase_a_voltage)
    - datetime_filter: Optional date and time to filter data (YYYY-MM-DD HH:MM:SS or YYYY-MM-DD HH:MM)

    Returns:
    - Filtered latest EMS live data with only the requested column plus essential fields
    """
    # Check if machine exists
    machine = Machine.get(id=machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail=f"Machine with ID {machine_id} not found")

    # Validate requested column based on actual MachineEMSLive entity fields
    available_columns = [
        "id", "machine_id", "timestamp", "status",
        "phase_a_voltage", "phase_b_voltage", "phase_c_voltage",
        "avg_phase_voltage", "line_ab_voltage", "line_bc_voltage",
        "line_ca_voltage", "avg_line_voltage",
        "phase_a_current", "phase_b_current", "phase_c_current",
        "avg_three_phase_current", "power_factor", "frequency",
        "total_instantaneous_power", "active_energy_delivered"
    ]

    if column_name not in available_columns:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid column: '{column_name}'. Available columns: {', '.join(available_columns)}"
        )

    # Always include these essential columns plus the requested column
    select_columns = ["id", "machine_id", "timestamp"]
    if column_name not in select_columns:
        select_columns.append(column_name)

    select_columns_str = ", ".join(select_columns)

    # Build SQL query
    query = f"""
    SELECT {select_columns_str} FROM ems.machine_ems_live 
    WHERE machine_id = $machine_id
    """

    params = {'machine_id': machine_id}

    # Add timestamp filter if provided
    if datetime_filter:
        try:
            # Strip any whitespace
            datetime_filter = datetime_filter.strip()

            # Check if we have seconds in the input
            if len(datetime_filter.split(':')) == 2:
                # If only HH:MM provided, add ":00" for seconds
                datetime_filter += ":00"

            # Parse the datetime
            filter_datetime = datetime.strptime(datetime_filter, "%Y-%m-%d %H:%M:%S")

            # Get the minute part for partial matching
            minute_start = filter_datetime.replace(second=0)
            minute_end = filter_datetime.replace(second=59)

            # Find records within that minute (partial matching)
            query += " AND timestamp BETWEEN $minute_start AND $minute_end"
            params['minute_start'] = minute_start
            params['minute_end'] = minute_end

        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="Invalid datetime format. Use YYYY-MM-DD HH:MM:SS or YYYY-MM-DD HH:MM format."
            )
    else:
        # Get the latest record if no specific timestamp
        query += " ORDER BY timestamp DESC LIMIT 1"

    # Execute raw SQL with parameters
    result_data = db.execute(query, params)

    # Convert raw result to proper dictionary
    column_names = [desc[0] for desc in result_data.description]
    rows = list(result_data)

    if not rows:
        if datetime_filter:
            raise HTTPException(
                status_code=404,
                detail=f"No live data available for Machine ID {machine_id} within the minute of {datetime_filter}"
            )
        else:
            raise HTTPException(
                status_code=404,
                detail=f"No live data available for Machine ID {machine_id}"
            )

    # Get the first row (closest match or most recent if multiple matches)
    row = rows[0]
    result = {}

    # Map column names to values
    for i, value in enumerate(row):
        result[column_names[i]] = value

    return result

@router.get("/history_data/{machine_id}", response_model=List[EMSDataModel])
@db_session
def get_machine_history_data(machine_id: int):
    """
    Fetch EMS history data for a specific machine

    Parameters:
    - machine_id: ID of the machine

    Returns:
    - List of EMS history data records for the specified machine
    """
    # Check if machine exists
    machine = Machine.get(id=machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail=f"Machine with ID {machine_id} not found")

    # Use a simpler query approach that avoids lambda functions
    history_data = list(
        MachineEMSHistory.select(machine_id=machine_id).order_by(MachineEMSHistory.timestamp.desc())[:100])

    # Check if any data was found
    if not history_data:
        raise HTTPException(status_code=404, detail=f"No history data available for Machine ID {machine_id}")

    # Convert entity objects to dictionaries
    result = [h.to_dict() for h in history_data]

    return result


@router.get("/history_data_by_date/{machine_id}", response_model=List[EMSDataModel])
@db_session
def get_machine_history_data_by_date(
        machine_id: int,
        start_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
        end_date: str = Query(..., description="End date (YYYY-MM-DD)")
):
    """
    Fetch EMS history data for a specific machine between start_date and end_date

    Parameters:
    - machine_id: ID of the machine
    - start_date: Start date to filter data (inclusive)
    - end_date: End date to filter data (inclusive)

    Returns:
    - List of EMS history data records for the specified machine and date range
    """
    # Check if machine exists
    machine = Machine.get(id=machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail=f"Machine with ID {machine_id} not found")

    # Parse and validate dates - handle potential formatting issues
    try:
        # Strip any whitespace that might be in the date strings
        start_date = start_date.strip()
        end_date = end_date.strip()

        # Parse the dates
        start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()

        # Convert dates to datetime for comparison
        # Set time to 00:00:00 for start_date and 23:59:59 for end_date to include the entire day
        start_datetime = datetime.combine(start_date_obj, datetime.min.time())
        end_datetime = datetime.combine(end_date_obj, datetime.max.time())

        # Validate date range
        if end_date_obj < start_date_obj:
            raise HTTPException(status_code=400, detail="End date cannot be before start date")
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD format.")

    # Using SQL directly to avoid decompilation issues with complex conditions
    query = f"""
    SELECT * FROM ems.machine_ems_history 
    WHERE machine_id = $machine_id 
    AND timestamp >= $start_datetime 
    AND timestamp <= $end_datetime 
    ORDER BY timestamp DESC
    """

    # Execute raw SQL with parameters
    history_data = db.execute(query,
                              {'machine_id': machine_id,
                               'start_datetime': start_datetime,
                               'end_datetime': end_datetime})

    # Convert raw result to proper dictionaries
    column_names = [desc[0] for desc in history_data.description]
    result = []

    for row in history_data:
        record = {}
        for i, value in enumerate(row):
            record[column_names[i]] = value
        result.append(record)

    # Check if any data was found
    if not result:
        raise HTTPException(status_code=404,
                            detail=f"No history data available for Machine ID {machine_id} in the specified date range")

    return result


@router.get("/filtered_history_data/{machine_id}", response_model=List[Dict])
@db_session
def get_filtered_machine_history_data(
        machine_id: int,
        start_date: str = Query(..., description="Start date (YYYY-MM-DD)"),
        end_date: str = Query(..., description="End date (YYYY-MM-DD)"),
        column_name: str = Query(..., description="Specific column to filter data (e.g., phase_a_voltage)")
):
    """
    Fetch EMS history data for a specific machine and a specific column between start_date and end_date

    Parameters:
    - machine_id: ID of the machine
    - start_date: Start date to filter data (inclusive)
    - end_date: End date to filter data (inclusive)
    - column_name: Name of the specific column to retrieve (e.g., phase_a_voltage)

    Returns:
    - List of EMS history data records with only the requested column plus essential fields
    """
    # Check if machine exists
    machine = Machine.get(id=machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail=f"Machine with ID {machine_id} not found")

    # Parse and validate dates - handle potential formatting issues
    try:
        # Strip any whitespace that might be in the date strings
        start_date = start_date.strip()
        end_date = end_date.strip()

        # Parse the dates
        start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()

        # Convert dates to datetime for comparison
        # Set time to 00:00:00 for start_date and 23:59:59 for end_date to include the entire day
        start_datetime = datetime.combine(start_date_obj, datetime.min.time())
        end_datetime = datetime.combine(end_date_obj, datetime.max.time())

        # Validate date range
        if end_date_obj < start_date_obj:
            raise HTTPException(status_code=400, detail="End date cannot be before start date")
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD format.")

    # Validate requested column based on actual MachineEMSHistory entity fields
    available_columns = [
        "id", "machine_id", "timestamp",
        "phase_a_voltage", "phase_b_voltage", "phase_c_voltage",
        "avg_phase_voltage", "line_ab_voltage", "line_bc_voltage",
        "line_ca_voltage", "avg_line_voltage",
        "phase_a_current", "phase_b_current", "phase_c_current",
        "avg_three_phase_current", "power_factor", "frequency",
        "total_instantaneous_power", "active_energy_delivered"
    ]

    if column_name not in available_columns:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid column: '{column_name}'. Available columns: {', '.join(available_columns)}"
        )

    # Always include these essential columns plus the requested column
    select_columns = "id, machine_id, timestamp, " + column_name
    if column_name in ["id", "machine_id", "timestamp"]:
        select_columns = "id, machine_id, timestamp"

    # Using SQL directly with selected columns
    query = f"""
    SELECT {select_columns} FROM ems.machine_ems_history 
    WHERE machine_id = $machine_id 
    AND timestamp >= $start_datetime 
    AND timestamp <= $end_datetime 
    ORDER BY timestamp DESC
    """

    # Execute raw SQL with parameters
    history_data = db.execute(query,
                              {'machine_id': machine_id,
                               'start_datetime': start_datetime,
                               'end_datetime': end_datetime})

    # Convert raw result to proper dictionaries
    column_names = [desc[0] for desc in history_data.description]
    result = []

    for row in history_data:
        record = {}
        for i, value in enumerate(row):
            record[column_names[i]] = value
        result.append(record)

    # Check if any data was found
    if not result:
        raise HTTPException(status_code=404,
                            detail=f"No history data available for Machine ID {machine_id} in the specified date range")

    return result




# ''''''''''''below is new shiftwise endpoint foe live table and history table


@router.get("/shiftwise_energy_history/{machine_id}")
@db_session
def get_shiftwise_energy_history(
        machine_id: int,
        column_name: str = Query(..., description="Column to filter by (first_shift, second_shift, third_shift, total_energy)")
):
    """
    Fetch shiftwise energy history data for a specific machine filtered by column name

    Parameters:
    - machine_id: ID of the machine
    - column_name: Column to filter by (first_shift, second_shift, third_shift, total_energy)

    Returns:
    - List of filtered records containing only timestamp, machine_id and the specified column
    """
    # Check if machine exists
    machine = Machine.get(id=machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail=f"Machine with ID {machine_id} not found")

    # Validate column name
    valid_columns = ["first_shift", "second_shift", "third_shift", "total_energy"]
    if column_name not in valid_columns:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid column name. Must be one of: {', '.join(valid_columns)}"
        )

    # Using SQL directly to select only the required columns
    query = f"""
    SELECT timestamp, {column_name}, machine_id 
    FROM ems.shiftwise_energy_history 
    WHERE machine_id = $machine_id 
    ORDER BY timestamp DESC
    """

    # Execute raw SQL with parameters
    history_data = db.execute(query, {'machine_id': machine_id})

    # Convert raw result to proper dictionaries
    column_names = [desc[0] for desc in history_data.description]
    result = []

    for row in history_data:
        record = {}
        for i, value in enumerate(row):
            record[column_names[i]] = value
        result.append(record)

    # Check if any data was found
    if not result:
        raise HTTPException(status_code=404,
                            detail=f"No shiftwise energy history data available for Machine ID {machine_id}")

    return result


@router.get("/shiftwise_energy_live/{machine_id}", response_model=ShiftwiseEnergyModel)
@db_session
def get_shiftwise_energy_live(machine_id: int):
    """
    Fetch current shiftwise energy live data for a specific machine

    Parameters:
    - machine_id: ID of the machine

    Returns:
    - Current shiftwise energy live record for the specified machine
    """
    # Check if machine exists
    machine = Machine.get(id=machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail=f"Machine with ID {machine_id} not found")

    # Query for the live data
    live_data = ShiftwiseEnergyLive.get(machine_id=machine_id)

    if not live_data:
        raise HTTPException(status_code=404,
                            detail=f"No live shiftwise energy data available for Machine ID {machine_id}")

    return live_data


@router.get("/shiftwise_energy_live", response_model=List[ShiftwiseEnergyModel])
@db_session
def get_all_shiftwise_energy_live():
    """
    Fetch all current shiftwise energy live data for all machines

    Returns:
    - List of current shiftwise energy live records for all machines
    """
    # Query for all live data
    live_data = list(ShiftwiseEnergyLive.select())

    if not live_data:
        raise HTTPException(status_code=404, detail="No live shiftwise energy data available")

    return live_data




@router.get("/filtering_shiftwiseenergy_columnwise_live/{machine_id}")
@db_session
def get_shiftwise_energy_live(
        machine_id: int,
        column_name: str = Query(..., description="Column to filter by (first_shift, second_shift, third_shift, total_energy)")
):
    """
    Fetch current shiftwise energy live data for a specific machine filtered by column name

    Parameters:
    - machine_id: ID of the machine
    - column_name: Column to filter by (first_shift, second_shift, third_shift, total_energy)

    Returns:
    - Filtered record containing only timestamp, machine_id and the specified column
    """
    # Check if machine exists
    machine = Machine.get(id=machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail=f"Machine with ID {machine_id} not found")

    # Validate column name
    valid_columns = ["first_shift", "second_shift", "third_shift", "total_energy"]
    if column_name not in valid_columns:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid column name. Must be one of: {', '.join(valid_columns)}"
        )

    # Using SQL directly to select only the required columns
    query = f"""
    SELECT timestamp, {column_name}, machine_id 
    FROM ems.shiftwise_energy_live 
    WHERE machine_id = $machine_id
    """

    # Execute raw SQL with parameters
    live_data = db.execute(query, {'machine_id': machine_id})

    # Convert raw result to proper dictionaries
    column_names = [desc[0] for desc in live_data.description]
    result = {}

    # Since we expect only one row for live data
    row = live_data.fetchone()
    if row:
        for i, value in enumerate(row):
            result[column_names[i]] = value
    else:
        raise HTTPException(status_code=404,
                        detail=f"No live shiftwise energy data available for Machine ID {machine_id}")

    return result






