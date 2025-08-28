from typing import Optional, List
from pony.orm import db_session, select
from datetime import datetime
from ..models.pdc import PDC
from ..models.master_order import Order


@db_session
def create_pdc_record(
        order_id: int,
        part_number: str,
        production_order: str,
        pdc_data: datetime,
        data_source: str,
        is_active: bool = True
) -> Optional[PDC]:
    """Create a new PDC record"""
    try:
        # Verify that the order exists
        order = Order.get(id=order_id)
        if not order:
            raise Exception(f"Order with ID {order_id} not found")

        pdc_record = PDC(
            order_id=order,
            part_number=part_number,
            production_order=production_order,
            pdc_data=pdc_data,
            data_source=data_source,
            is_active=is_active,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        return pdc_record
    except Exception as e:
        raise Exception(f"Error creating PDC record: {str(e)}")


@db_session
def get_pdc_by_part_number_and_po(part_number: str, production_order: str) -> List[PDC]:
    """Get PDC records by part number and production order"""
    try:
        records = select(
            pdc for pdc in PDC
            if pdc.part_number == part_number
            and pdc.production_order == production_order
            and pdc.is_active == True
        )
        return list(records)
    except Exception as e:
        raise Exception(f"Error retrieving PDC records: {str(e)}")


@db_session
def get_all_pdc_records() -> List[PDC]:
    """Get all active PDC records"""
    try:
        records = select(pdc for pdc in PDC if pdc.is_active == True)
        return list(records)
    except Exception as e:
        raise Exception(f"Error retrieving all PDC records: {str(e)}")


@db_session
def get_pdc_by_id(pdc_id: int) -> Optional[PDC]:
    """Get PDC record by ID"""
    try:
        return PDC.get(id=pdc_id, is_active=True)
    except Exception as e:
        raise Exception(f"Error retrieving PDC record: {str(e)}")


@db_session
def update_pdc_record(pdc_id: int, **kwargs) -> Optional[PDC]:
    """Update PDC record"""
    try:
        pdc_record = PDC.get(id=pdc_id, is_active=True)
        if not pdc_record:
            return None

        # Update fields if provided
        for field, value in kwargs.items():
            if hasattr(pdc_record, field) and value is not None:
                setattr(pdc_record, field, value)

        # Update the updated_at timestamp
        pdc_record.updated_at = datetime.utcnow()

        return pdc_record
    except Exception as e:
        raise Exception(f"Error updating PDC record: {str(e)}")


@db_session
def delete_pdc_record(pdc_id: int) -> bool:
    """Soft delete PDC record by setting is_active to False"""
    try:
        pdc_record = PDC.get(id=pdc_id, is_active=True)
        if not pdc_record:
            return False

        pdc_record.is_active = False
        pdc_record.updated_at = datetime.utcnow()

        return True
    except Exception as e:
        raise Exception(f"Error deleting PDC record: {str(e)}")


@db_session
def upsert_pdc_record(
        order_id: int,
        part_number: str,
        production_order: str,
        pdc_data: datetime,
        data_source: str,
        is_active: bool = True
) -> PDC:
    """Create or update a single active PDC for (part_number, production_order).

    If one or more active records exist, the most recently updated one is updated
    with the new data and all other active duplicates are deactivated. If none exist,
    a new record is created.
    """
    # Ensure the order exists
    order = Order.get(id=order_id)
    if not order:
        raise Exception(f"Order with ID {order_id} not found")

    # Fetch active records for this key
    active_records = list(select(pdc for pdc in PDC if
                                 pdc.part_number == part_number and pdc.production_order == production_order and pdc.is_active == True))

    if not active_records:
        # No active record: create one
        new_rec = PDC(
            order_id=order,
            part_number=part_number,
            production_order=production_order,
            pdc_data=pdc_data,
            data_source=data_source,
            is_active=is_active,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        return new_rec

    # Sort by updated_at desc; fall back to id desc
    active_records.sort(key=lambda r: (r.updated_at, r.id), reverse=True)
    primary = active_records[0]

    # Update primary
    primary.pdc_data = pdc_data
    primary.data_source = data_source
    primary.is_active = is_active
    primary.order_id = order
    primary.updated_at = datetime.utcnow()

    # Deactivate duplicates
    for dup in active_records[1:]:
        dup.is_active = False
        dup.updated_at = datetime.utcnow()

    return primary


@db_session
def delete_pdc_by_production_order(production_order: str) -> bool:
    """Delete all PDC records for a specific production order by setting is_active to False"""
    try:
        # Find all active PDC records for this production order
        pdc_records = list(select(pdc for pdc in PDC 
                                 if pdc.production_order == production_order 
                                 and pdc.is_active == True))
        
        if not pdc_records:
            return False  # No records to delete
        
        # Soft delete all records by setting is_active to False
        for pdc_record in pdc_records:
            pdc_record.is_active = False
            pdc_record.updated_at = datetime.utcnow()
        
        return True
    except Exception as e:
        raise Exception(f"Error deleting PDC records for production order {production_order}: {str(e)}")
