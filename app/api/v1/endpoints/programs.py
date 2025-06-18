from fastapi import APIRouter, HTTPException, Depends
from pony.orm import db_session, select
from datetime import datetime, timedelta
import random
import string

from app.models import Program, Operation


router = APIRouter(prefix="/api/v1/programs", tags=["programs"])

def generate_program_details(operation):
    """
    Generate program details based on operation

    Args:
        operation (Operation): The operation to generate program for

    Returns:
        dict: Program details including program name, number, and version
    """

    # Generate a unique program name
    def generate_unique_program_name(operation):
        # Format: [WorkCenter Code]-[Part Number]-[Operation Number]
        work_center_code = operation.work_center.code
        part_number = operation.order.part_number
        return f"{work_center_code}-{part_number}-OP{operation.operation_number}"

    def generate_program_number():
        # Generate a random alphanumeric program number
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

    return {
        'program_name': generate_unique_program_name(operation),
        'program_number': generate_program_number(),
        'version': '1.0',
        'update_date': datetime.now() - timedelta(days=random.randint(0, 30))
    }


@router.post("/generate-programs")
@db_session
async def generate_programs_for_operations():
    """
    Endpoint to generate programs for operations without existing programs

    Returns:
        dict: Summary of generated programs
    """
    # Find operations without programs
    operations_without_programs = select(op for op in Operation
                                         if not op.programs)

    generated_programs = []

    for operation in operations_without_programs:
        # Generate program details
        program_details = generate_program_details(operation)

        # Create new Program
        new_program = Program(
            operation=operation,
            program_name=program_details['program_name'],
            program_number=program_details['program_number'],
            version=program_details['version'],
            update_date=program_details['update_date']
        )

        generated_programs.append({
            'operation_id': operation.id,
            'program_name': new_program.program_name,
            'program_number': new_program.program_number
        })

    return {
        "total_programs_generated": len(generated_programs),
        "programs": generated_programs
    }
