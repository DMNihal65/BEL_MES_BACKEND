from fastapi import APIRouter, HTTPException, Depends, Query, Path, status
from pony.orm import db_session, select, commit
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.models.master_order import Program, Operation, Order, OrderTool
from app.schemas.toolsprograms import (
    ProgramCreate, ProgramResponse, ProgramUpdate,
    OrderToolCreate, OrderToolResponse, OrderToolUpdate,

    ToolAndFixtureCreate, ToolAndFixtureResponse, ToolAndFixtureUpdate
)
from app.core.security import get_current_user

# Create the router
router = APIRouter(prefix="/api/v1/toolsprograms", tags=["tools and programs"])


# -------------------------------------------------------------------
# Simple Program endpoints
# -------------------------------------------------------------------

@router.post("/programs/", response_model=ProgramResponse, status_code=status.HTTP_201_CREATED)
@db_session
def create_program(program: ProgramCreate):
    """
    Create a new program for an operation.
    """
    # First check if order exists
    order = Order.get(id=program.order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order with ID {program.order_id} not found"
        )

    # Then check if operation exists
    operation = Operation.get(id=program.operation_id)
    if not operation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Operation with ID {program.operation_id} not found"
        )

    # Verify that the operation belongs to the specified order
    if operation.order.id != program.order_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Operation with ID {program.operation_id} does not belong to Order with ID {program.order_id}"
        )

    # Create new program
    new_program = Program(
        operation=operation,
        program_name=program.program_name,
        program_number=program.program_number,
        version=program.version,
        update_date=datetime.now()
    )

    commit()

    # Return with both operation_id and order_id
    return {
        "id": new_program.id,
        "operation_id": new_program.operation.id,
        "order_id": new_program.operation.order.id,  # Include order_id from the operation's relation
        "program_name": new_program.program_name,
        "program_number": new_program.program_number,
        "version": new_program.version,
        "update_date": new_program.update_date
    }


@router.get("/programs/", response_model=List[ProgramResponse])
@db_session
def get_programs(
        order_id: Optional[int] = Query(None, description="Filter by order ID"),
        operation_id: Optional[int] = Query(None, description="Filter by operation ID")
):
    """
    Get all programs with optional filtering.
    """
    try:
        # Log query parameters to help with debugging
        print(f"Querying programs with filters - order_id: {order_id}, operation_id: {operation_id}")

        # Get all programs regardless of filters first
        all_programs = list(Program.select())
        filtered_programs = []

        # Apply filters after fetching to avoid complex PonyORM queries
        for program in all_programs:
            program_order_id = program.operation.order.id

            # Apply filters
            if order_id is not None and operation_id is not None:
                # Both filters provided
                if program_order_id == order_id and program.operation.id == operation_id:
                    filtered_programs.append(program)
            elif order_id is not None:
                # Only order_id filter
                if program_order_id == order_id:
                    filtered_programs.append(program)
            elif operation_id is not None:
                # Only operation_id filter
                if program.operation.id == operation_id:
                    filtered_programs.append(program)
            else:
                # No filters, include all
                filtered_programs.append(program)

        print(f"Filtered programs count: {len(filtered_programs)}")

        # Convert to a list of dictionaries with both operation_id and order_id
        result = []
        for p in filtered_programs:
            result.append({
                "id": p.id,
                "operation_id": p.operation.id,
                "order_id": p.operation.order.id,  # Include order_id from the operation's relation
                "program_name": p.program_name,
                "program_number": p.program_number,
                "version": p.version,
                "update_date": p.update_date
            })

        print(f"Returning {len(result)} programs")
        return result
    except Exception as e:
        # Log any errors that occur
        print(f"Error in get_programs: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving programs: {str(e)}"
        )


@router.get("/programs/{program_id}", response_model=ProgramResponse)
@db_session
def get_program(program_id: int = Path(..., description="The ID of the program to retrieve")):
    """
    Get a specific program by ID.
    """
    try:
        program = Program.get(id=program_id)
        if not program:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Program with ID {program_id} not found"
            )

        # Return program data including the order_id
        return {
            "id": program.id,
            "operation_id": program.operation.id,
            "order_id": program.operation.order.id,  # Include order_id from operation relation
            "program_name": program.program_name,
            "program_number": program.program_number,
            "version": program.version,
            "update_date": program.update_date
        }
    except Exception as e:
        if "Program with ID" in str(e):
            raise e
        print(f"Error in get_program: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving program: {str(e)}"
        )


@router.put("/programs/{program_id}", response_model=ProgramResponse)
@db_session
def update_program(
        program_id: int,
        program_update: ProgramUpdate
):
    """
    Update a program by ID.
    """
    try:
        program = Program.get(id=program_id)
        if not program:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Program with ID {program_id} not found"
            )

        # Update fields if provided
        if program_update.program_name is not None:
            program.program_name = program_update.program_name

        if program_update.program_number is not None:
            program.program_number = program_update.program_number

        if program_update.version is not None:
            program.version = program_update.version

        # Always update the update_date when a program is modified
        program.update_date = datetime.now()

        commit()

        # Return updated program data with order_id
        return {
            "id": program.id,
            "operation_id": program.operation.id,
            "order_id": program.operation.order.id,  # Include order_id from operation relation
            "program_name": program.program_name,
            "program_number": program.program_number,
            "version": program.version,
            "update_date": program.update_date
        }
    except Exception as e:
        if "Program with ID" in str(e):
            raise e
        print(f"Error in update_program: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating program: {str(e)}"
        )


@router.delete("/programs/{program_id}", status_code=status.HTTP_204_NO_CONTENT)
@db_session
def delete_program(program_id: int):
    """
    Delete a program by ID.
    """
    program = Program.get(id=program_id)
    if not program:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Program with ID {program_id} not found"
        )

    program.delete()
    commit()

    return None


# -------------------------------------------------------------------
# Simple Tool and Fixture endpoints
# -------------------------------------------------------------------




# -------------------------------------------------------------------
# OrderTool endpoints
# -------------------------------------------------------------------

@router.post("/ordertools/", response_model=OrderToolResponse, status_code=status.HTTP_201_CREATED)
@db_session
def create_order_tool(tool: OrderToolCreate):
    """
    Create a new tool for an order.
    """
    # Check if order exists
    order = Order.get(id=tool.order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order with ID {tool.order_id} not found"
        )

    # Check if operation exists if operation_id is provided
    operation = None
    if tool.operation_id:
        operation = Operation.get(id=tool.operation_id)
        if not operation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Operation with ID {tool.operation_id} not found"
            )

        # Ensure operation belongs to order
        if operation.order.id != order.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Operation {operation.id} does not belong to Order {order.id}"
            )

    # Create new tool
    new_tool = OrderTool(
        order=order,
        operation=operation,
        tool_name=tool.tool_name,
        tool_number=tool.tool_number,
        bel_partnumber=tool.bel_partnumber,
        description=tool.description,
        quantity=tool.quantity
    )

    commit()

    return {
        "id": new_tool.id,
        "order_id": new_tool.order.id,
        "operation_id": new_tool.operation.id if new_tool.operation else None,
        "tool_name": new_tool.tool_name,
        "tool_number": new_tool.tool_number,
        "bel_partnumber": new_tool.bel_partnumber,
        "description": new_tool.description,
        "quantity": new_tool.quantity,
        "created_at": new_tool.created_at,
        "updated_at": new_tool.updated_at
    }


@router.get("/ordertools/", response_model=List[OrderToolResponse])
@db_session
def get_order_tools(
        order_id: Optional[int] = Query(None, description="Filter by order ID"),
        operation_id: Optional[int] = Query(None, description="Filter by operation ID")
):
    """
    Get all tools with optional filtering.
    """
    try:
        # Get all tools first
        all_tools = list(OrderTool.select())

        # Apply filters in memory if needed
        filtered_tools = []
        for tool in all_tools:
            # Check if tool matches the filters
            matches_order = order_id is None or tool.order.id == order_id
            matches_operation = operation_id is None or (tool.operation and tool.operation.id == operation_id)

            if matches_order and matches_operation:
                filtered_tools.append(tool)

        # Convert to response format
        return [{
            "id": tool.id,
            "order_id": tool.order.id,
            "operation_id": tool.operation.id if tool.operation else None,
            "tool_name": tool.tool_name,
            "tool_number": tool.tool_number,
            "bel_partnumber": tool.bel_partnumber,
            "description": tool.description,
            "quantity": tool.quantity,
            "created_at": tool.created_at,
            "updated_at": tool.updated_at
        } for tool in filtered_tools]
    except Exception as e:
        print(f"Error in get_order_tools: {str(e)}")  # Add logging
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving tools: {str(e)}"
        )


@router.get("/ordertools/{tool_id}", response_model=OrderToolResponse)
@db_session
def get_order_tool(tool_id: int):
    """
    Get a specific tool by ID.
    """
    tool = OrderTool.get(id=tool_id)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool with ID {tool_id} not found"
        )

    return {
        "id": tool.id,
        "order_id": tool.order.id,
        "operation_id": tool.operation.id if tool.operation else None,
        "tool_name": tool.tool_name,
        "tool_number": tool.tool_number,
        "bel_partnumber": tool.bel_partnumber,
        "description": tool.description,
        "quantity": tool.quantity,
        "created_at": tool.created_at,
        "updated_at": tool.updated_at
    }


@router.put("/ordertools/{tool_id}", response_model=OrderToolResponse)
@db_session
def update_order_tool(tool_id: int, tool_update: OrderToolUpdate):
    """
    Update a tool by ID.
    """
    tool = OrderTool.get(id=tool_id)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool with ID {tool_id} not found"
        )

    try:
        # Update fields if provided
        if tool_update.tool_name is not None:
            tool.tool_name = tool_update.tool_name

        if tool_update.tool_number is not None:
            tool.tool_number = tool_update.tool_number

        if tool_update.bel_partnumber is not None:
            tool.bel_partnumber = tool_update.bel_partnumber

        if tool_update.description is not None:
            tool.description = tool_update.description

        if tool_update.quantity is not None:
            tool.quantity = tool_update.quantity

        if tool_update.operation_id is not None:
            operation = Operation.get(id=tool_update.operation_id)
            if not operation:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Operation with ID {tool_update.operation_id} not found"
                )
            tool.operation = operation

        commit()

        return {
            "id": tool.id,
            "order_id": tool.order.id,
            "operation_id": tool.operation.id if tool.operation else None,
            "tool_name": tool.tool_name,
            "tool_number": tool.tool_number,
            "bel_partnumber": tool.bel_partnumber,
            "description": tool.description,
            "quantity": tool.quantity,
            "created_at": tool.created_at,
            "updated_at": tool.updated_at
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating tool: {str(e)}"
        )


@router.delete("/ordertools/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
@db_session
def delete_order_tool(tool_id: int):
    """
    Delete a tool by ID.
    """
    tool = OrderTool.get(id=tool_id)
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool with ID {tool_id} not found"
        )

    tool.delete()
    commit()

    return None