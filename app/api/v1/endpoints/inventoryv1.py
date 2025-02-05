# endpoints.py
from fastapi import APIRouter, HTTPException, Depends
from typing import List
from datetime import datetime, timedelta, timezone
from pony.orm import db_session, commit, select, flush, rollback
from datetime import datetime
from app.schemas.inventoryv1 import (InventoryCategoryResponse,
                                     InventoryCategoryCreate,
                                     InventorySubCategoryResponse,
                                     InventorySubCategoryCreate,
                                     InventoryRequestResponse,
                                     InventoryItemResponse,
                                     InventoryItemCreate,
                                     CalibrationScheduleResponse, CalibrationScheduleCreate,
                                     InventoryRequestCreate, InventoryTransactionResponse, InventoryTransactionCreate,
                                     InventoryCategoryUpdate, InventorySubCategoryUpdate, InventoryItemUpdate,
                                     CalibrationScheduleUpdate,
                                     CalibrationHistoryResponse, CalibrationHistoryCreate, InventoryRequestUpdate,
                                     StatusCount, CalibrationDue, TransactionSummary,
                                     BulkInventoryItemCreate, TransactionType,
                                     )

from app.models.inventoryv1 import (
    InventoryCategory,
    InventorySubCategory,
    InventoryItem,
    CalibrationSchedule,
    CalibrationHistory,
    InventoryRequest,
    InventoryTransaction,
    
)
from app.models.user import User
from app.core.security import get_current_user  # Import the auth dependency
from app.models.master_order import Order,Operation

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

# Inventory Category Endpoints
@router.post("/categories/", response_model=InventoryCategoryResponse)
@db_session
def create_category(category: InventoryCategoryCreate):
    """
    Create a new inventory category.
    
    Sample request:
    ```json
    {
        "name": "Tools",
        "description": "All manufacturing tools",
        "created_by": 1
    }
    ```
    """
    user = User.get(id=category.created_by)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    new_category = InventoryCategory(
        name=category.name,
        description=category.description,
        created_by=user,
        created_at=datetime.utcnow()
    )
    commit()
    return new_category.to_dict()

@router.get("/categories/{category_id}", response_model=InventoryCategoryResponse)
@db_session
def get_category(category_id: int):
    category = InventoryCategory.get(id=category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    return {
        "id": category.id,
        "name": category.name,
        "description": category.description,
        "created_at": category.created_at,
        "created_by": category.created_by.id
    }

# Inventory SubCategory Endpoints
@router.post("/subcategories/", response_model=InventorySubCategoryResponse)
@db_session
def create_subcategory(subcategory: InventorySubCategoryCreate):
    """
    Create a new inventory subcategory.
    
    Sample request:
    ```json
    {
        "name": "End Mills",
        "description": "Cutting tools for milling operations",
        "dynamic_fields": {
            "diameter": {"type": "float", "unit": "mm", "required": true},
            "flutes": {"type": "integer", "required": true},
            "length": {"type": "float", "unit": "mm", "required": true},
            "coating": {"type": "string", "required": false}
        },
        "category_id": 1,
        "created_by": 1
    }
    ```
    """
    category = InventoryCategory.get(id=subcategory.category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    user = User.get(id=subcategory.created_by)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    new_subcategory = InventorySubCategory(
        category=category,
        name=subcategory.name,
        description=subcategory.description,
        dynamic_fields=subcategory.dynamic_fields,
        created_by=user,
        created_at=datetime.utcnow()
    )
    commit()
    
    # Create a response dictionary with the correct structure
    response_data = {
        "id": new_subcategory.id,
        "name": new_subcategory.name,
        "description": new_subcategory.description,
        "dynamic_fields": new_subcategory.dynamic_fields,
        "category_id": category.id,  # Explicitly include category_id
        "created_at": new_subcategory.created_at,
        "created_by": new_subcategory.created_by.id
    }
    return response_data

# Inventory Item Endpoints
@router.post("/items/", response_model=InventoryItemResponse)
@db_session
def create_item(item: InventoryItemCreate):
    """
    Create a new inventory item.
    """
    try:
        # Check if item code already exists
        existing_item = InventoryItem.get(item_code=item.item_code)
        if existing_item:
            raise HTTPException(
                status_code=400,
                detail=f"Item with code '{item.item_code}' already exists"
            )

        # Validate subcategory
        subcategory = InventorySubCategory.get(id=item.subcategory_id)
        if not subcategory:
            raise HTTPException(status_code=404, detail="Subcategory not found")

        # Validate user
        user = User.get(id=item.created_by)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Validate quantities
        if item.available_quantity > item.quantity:
            raise HTTPException(
                status_code=400,
                detail="Available quantity cannot be greater than total quantity"
            )

        # Create new item
        new_item = InventoryItem(
            subcategory=subcategory,
            item_code=item.item_code,
            dynamic_data=item.dynamic_data,
            quantity=item.quantity,
            available_quantity=item.available_quantity,
            status=item.status.value,
            created_by=user,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        flush()  # Flush to get the ID before commit

        response_data = {
            "id": new_item.id,
            "item_code": new_item.item_code,
            "dynamic_data": new_item.dynamic_data,
            "quantity": new_item.quantity,
            "available_quantity": new_item.available_quantity,
            "status": new_item.status,
            "subcategory_id": subcategory.id,
            "created_at": new_item.created_at,
            "updated_at": new_item.updated_at,
            "created_by": user.id
        }
        
        commit()
        return response_data

    except HTTPException as he:
        raise he
    except Exception as e:
        if "duplicate key value violates unique constraint" in str(e):
            raise HTTPException(
                status_code=400,
                detail=f"Item with code '{item.item_code}' already exists"
            )
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/items/bulk/", response_model=List[InventoryItemResponse])
@db_session
def create_bulk_items(bulk_items: BulkInventoryItemCreate):
    """
    Create multiple inventory items in bulk.
    """
    try:
        # Validate subcategory
        subcategory = InventorySubCategory.get(id=bulk_items.subcategory_id)
        if not subcategory:
            raise HTTPException(status_code=404, detail="Subcategory not found")

        # Validate user
        user = User.get(id=bulk_items.created_by)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Check for duplicate item codes within the bulk request
        item_codes = [item['item_code'] for item in bulk_items.items]
        if len(item_codes) != len(set(item_codes)):
            raise HTTPException(
                status_code=400,
                detail="Duplicate item codes found in the request"
            )

        # Check for existing item codes in database
        existing_codes = select(i.item_code for i in InventoryItem 
                              if i.item_code in item_codes)[:]
        if existing_codes:
            raise HTTPException(
                status_code=400,
                detail=f"Items with codes {existing_codes} already exist"
            )

        created_items = []
        for item_data in bulk_items.items:
            # Validate quantities
            if item_data['available_quantity'] > item_data['quantity']:
                raise HTTPException(
                    status_code=400,
                    detail=f"Available quantity cannot be greater than total quantity for item {item_data['item_code']}"
                )

            # Create new item
            new_item = InventoryItem(
                subcategory=subcategory,
                item_code=item_data['item_code'],
                dynamic_data=item_data['dynamic_data'],
                quantity=item_data['quantity'],
                available_quantity=item_data['available_quantity'],
                status=item_data['status'],
                created_by=user,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            created_items.append({
                "id": new_item.id,
                "item_code": new_item.item_code,
                "dynamic_data": new_item.dynamic_data,
                "quantity": new_item.quantity,
                "available_quantity": new_item.available_quantity,
                "status": new_item.status,
                "subcategory_id": subcategory.id,
                "created_at": new_item.created_at,
                "updated_at": new_item.updated_at,
                "created_by": user.id
            })

        commit()
        return created_items

    except HTTPException as he:
        rollback()
        raise he
    except Exception as e:
        rollback()
        if "duplicate key value violates unique constraint" in str(e):
            raise HTTPException(
                status_code=400,
                detail="One or more item codes already exist in the database"
            )
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while creating items: {str(e)}"
        )

# Calibration Schedule Endpoints
@router.post("/calibrations/", response_model=CalibrationScheduleResponse)
@db_session
def create_calibration_schedule(calibration: CalibrationScheduleCreate):
    """
    Create a new calibration schedule.
    
    Sample request:
    ```json
    {
        "calibration_type": "Dimensional",
        "frequency_days": 90,
        "last_calibration": "2024-01-01T00:00:00Z",
        "next_calibration": "2024-04-01T00:00:00Z",
        "remarks": "Regular calibration schedule",
        "inventory_item_id": 1,
        "created_by": 1
    }
    ```
    """
    item = InventoryItem.get(id=calibration.inventory_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    
    user = User.get(id=calibration.created_by)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Validate that next_calibration is after last_calibration
    if calibration.last_calibration and calibration.next_calibration <= calibration.last_calibration:
        raise HTTPException(
            status_code=400,
            detail="Next calibration date must be after last calibration date"
        )
    
    new_calibration = CalibrationSchedule(
        inventory_item=item,
        calibration_type=calibration.calibration_type,
        frequency_days=calibration.frequency_days,
        last_calibration=calibration.last_calibration,
        next_calibration=calibration.next_calibration,
        remarks=calibration.remarks,
        created_by=user,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    commit()
    
    response_data = {
        "id": new_calibration.id,
        "calibration_type": new_calibration.calibration_type,
        "frequency_days": new_calibration.frequency_days,
        "last_calibration": new_calibration.last_calibration,
        "next_calibration": new_calibration.next_calibration,
        "remarks": new_calibration.remarks,
        "inventory_item_id": item.id,
        "created_at": new_calibration.created_at,
        "updated_at": new_calibration.updated_at,
        "created_by": user.id
    }
    return response_data

# Inventory Request Endpoints
@router.post("/requests/", response_model=InventoryRequestResponse)
@db_session
def create_inventory_request(
    request: InventoryRequestCreate,
    current_user: User = Depends(get_current_user)
):
    """
    Create a new inventory request.
    """
    try:
        # Get current time in UTC
        current_time = datetime.now(timezone.utc)
        
        # Get the item
        item = InventoryItem.get(id=request.inventory_item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Inventory item not found")
        
        # Validate available quantity
        if request.quantity > item.available_quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Requested quantity ({request.quantity}) exceeds available quantity ({item.available_quantity})"
            )
        
        # Get order
        order = Order.get(id=request.order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        
        # Get operation if provided
        operation = None
        if request.operation_id:
            operation = Operation.get(id=request.operation_id)
            if not operation:
                raise HTTPException(status_code=404, detail="Operation not found")
        
        # Ensure expected_return_date is in UTC
        expected_return_date = request.expected_return_date.replace(tzinfo=timezone.utc)
        
        # Create new request
        new_request = InventoryRequest(
            inventory_item=item,
            requested_by=User.get(id=current_user.id),
            order=order,
            operation=operation,
            quantity=request.quantity,
            purpose=request.purpose,
            status=request.status.value,
            expected_return_date=expected_return_date,
            remarks=request.remarks,
            created_at=current_time,
            updated_at=current_time,
            approved_by=None,
            approved_at=None
        )
        
        flush()
        
        response_data = {
            "id": new_request.id,
            "inventory_item_id": item.id,
            "requested_by": current_user.id,
            "order_id": order.id,
            "operation_id": operation.id if operation else None,
            "quantity": new_request.quantity,
            "purpose": new_request.purpose,
            "status": new_request.status,
            "expected_return_date": new_request.expected_return_date,
            "actual_return_date": None,
            "remarks": new_request.remarks,
            "created_at": new_request.created_at,
            "updated_at": new_request.updated_at,
            "approved_by": None,
            "approved_at": None
        }
        
        commit()
        return response_data

    except HTTPException as he:
        rollback()
        raise he
    except Exception as e:
        rollback()
        raise HTTPException(status_code=500, detail=str(e))

# Inventory Transaction Endpoints
@router.post("/transactions/", response_model=InventoryTransactionResponse)
@db_session
def create_transaction(transaction: InventoryTransactionCreate):
    """
    Create a new inventory transaction.
    
    Sample request:
    ```json
    {
        "inventory_item_id": 1,
        "transaction_type": "Issue",
        "quantity": 2,
        "performed_by": 1,
        "reference_request_id": 1,
        "remarks": "Issued for milling operation"
    }
    ```
    """
    item = InventoryItem.get(id=transaction.inventory_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    
    # Validate transaction quantity based on type
    if transaction.transaction_type == TransactionType.ISSUE:
        if transaction.quantity > item.available_quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Issue quantity ({transaction.quantity}) exceeds available quantity ({item.available_quantity})"
            )
        item.available_quantity -= transaction.quantity
    elif transaction.transaction_type == TransactionType.RETURN:
        if transaction.quantity > (item.quantity - item.available_quantity):
            raise HTTPException(
                status_code=400,
                detail="Return quantity exceeds issued quantity"
            )
        item.available_quantity += transaction.quantity
    
    new_transaction = InventoryTransaction(
        inventory_item=item,
        transaction_type=transaction.transaction_type.value,
        quantity=transaction.quantity,
        reference_request=transaction.reference_request_id,
        performed_by=User[transaction.performed_by],
        remarks=transaction.remarks,
        created_at=datetime.utcnow()
    )
    commit()
    
    response_data = {
        "id": new_transaction.id,
        "inventory_item_id": item.id,
        "transaction_type": new_transaction.transaction_type,
        "quantity": new_transaction.quantity,
        "reference_request_id": new_transaction.reference_request.id if new_transaction.reference_request else None,
        "performed_by": new_transaction.performed_by.id,
        "remarks": new_transaction.remarks,
        "created_at": new_transaction.created_at
    }
    return response_data

# Inventory Category Endpoints
@router.get("/categories/", response_model=List[InventoryCategoryResponse])
@db_session
def get_all_categories():
    categories = select(c for c in InventoryCategory)[:]
    return [
        {
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "created_at": c.created_at,
            "created_by": c.created_by.id
        }
        for c in categories
    ]

@router.put("/categories/{category_id}", response_model=InventoryCategoryResponse)
@db_session
def update_category(category_id: int, category: InventoryCategoryUpdate):
    db_category = InventoryCategory.get(id=category_id)
    if not db_category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    if category.name is not None:
        db_category.name = category.name
    if category.description is not None:
        db_category.description = category.description
    
    db_category.updated_at = datetime.utcnow()
    commit()
    return db_category.to_dict()

@router.delete("/categories/{category_id}", status_code=204)
@db_session
def delete_category(category_id: int):
    category = InventoryCategory.get(id=category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    category.delete()
    commit()

# Inventory SubCategory Endpoints
@router.get("/subcategories/", response_model=List[InventorySubCategoryResponse])
@db_session
def get_all_subcategories():
    subcategories = select(s for s in InventorySubCategory)[:]
    return [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "dynamic_fields": s.dynamic_fields,
            "category_id": s.category.id,
            "created_at": s.created_at,
            "created_by": s.created_by.id
        }
        for s in subcategories
    ]

@router.get("/subcategories/{subcategory_id}", response_model=InventorySubCategoryResponse)
@db_session
def get_subcategory(subcategory_id: int):
    subcategory = InventorySubCategory.get(id=subcategory_id)
    if not subcategory:
        raise HTTPException(status_code=404, detail="Subcategory not found")
    return {
        "id": subcategory.id,
        "name": subcategory.name,
        "description": subcategory.description,
        "dynamic_fields": subcategory.dynamic_fields,
        "category_id": subcategory.category.id,
        "created_at": subcategory.created_at,
        "created_by": subcategory.created_by.id
    }

@router.put("/subcategories/{subcategory_id}", response_model=InventorySubCategoryResponse)
@db_session
def update_subcategory(subcategory_id: int, subcategory: InventorySubCategoryUpdate):
    db_subcategory = InventorySubCategory.get(id=subcategory_id)
    if not db_subcategory:
        raise HTTPException(status_code=404, detail="Subcategory not found")
    
    if subcategory.name is not None:
        db_subcategory.name = subcategory.name
    if subcategory.description is not None:
        db_subcategory.description = subcategory.description
    if subcategory.dynamic_fields is not None:
        db_subcategory.dynamic_fields = subcategory.dynamic_fields
    if subcategory.category_id is not None:
        new_category = InventoryCategory.get(id=subcategory.category_id)
        if not new_category:
            raise HTTPException(status_code=404, detail="New category not found")
        db_subcategory.category = new_category
    
    db_subcategory.updated_at = datetime.utcnow()
    commit()
    return db_subcategory.to_dict()

@router.delete("/subcategories/{subcategory_id}", status_code=204)
@db_session
def delete_subcategory(subcategory_id: int):
    subcategory = InventorySubCategory.get(id=subcategory_id)
    if not subcategory:
        raise HTTPException(status_code=404, detail="Subcategory not found")
    subcategory.delete()
    commit()

# Inventory Item Endpoints
@router.get("/items/", response_model=List[InventoryItemResponse])
@db_session
def get_all_items():
    items = select(i for i in InventoryItem)[:]
    response_data = []
    for item in items:
        response_data.append({
            "id": item.id,
            "item_code": item.item_code,
            "dynamic_data": item.dynamic_data,
            "quantity": item.quantity,
            "available_quantity": item.available_quantity,
            "status": item.status,
            "subcategory_id": item.subcategory.id,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "created_by": item.created_by.id
        })
    return response_data

@router.get("/items/{item_id}", response_model=InventoryItemResponse)
@db_session
def get_item(item_id: int):
    item = InventoryItem.get(id=item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return {
        "id": item.id,
        "item_code": item.item_code,
        "dynamic_data": item.dynamic_data,
        "quantity": item.quantity,
        "available_quantity": item.available_quantity,
        "status": item.status,
        "subcategory_id": item.subcategory.id,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "created_by": item.created_by.id
    }

@router.put("/items/{item_id}", response_model=InventoryItemResponse)
@db_session
def update_item(item_id: int, item: InventoryItemUpdate):
    db_item = InventoryItem.get(id=item_id)
    if not db_item:
        raise HTTPException(status_code=404, detail="Item not found")
    
    if item.item_code is not None:
        db_item.item_code = item.item_code
    if item.dynamic_data is not None:
        db_item.dynamic_data = item.dynamic_data
    if item.quantity is not None:
        db_item.quantity = item.quantity
    if item.available_quantity is not None:
        db_item.available_quantity = item.available_quantity
    if item.status is not None:
        db_item.status = item.status.value
    if item.subcategory_id is not None:
        new_subcat = InventorySubCategory.get(id=item.subcategory_id)
        if not new_subcat:
            raise HTTPException(status_code=404, detail="Subcategory not found")
        db_item.subcategory = new_subcat
    
    db_item.updated_at = datetime.utcnow()
    commit()
    
    return {
        "id": db_item.id,
        "item_code": db_item.item_code,
        "dynamic_data": db_item.dynamic_data,
        "quantity": db_item.quantity,
        "available_quantity": db_item.available_quantity,
        "status": db_item.status,
        "subcategory_id": db_item.subcategory.id,  # Ensure subcategory_id is included
        "created_at": db_item.created_at,
        "updated_at": db_item.updated_at,
        "created_by": db_item.created_by.id
    }

@router.delete("/items/{item_id}", status_code=204)
@db_session
def delete_item(item_id: int):
    item = InventoryItem.get(id=item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    item.delete()
    commit()

# Calibration Schedule Endpoints
@router.get("/calibrations/", response_model=List[CalibrationScheduleResponse])
@db_session
def get_all_calibrations():
    calibrations = select(c for c in CalibrationSchedule)[:]
    return [
        {
            "id": c.id,
            "calibration_type": c.calibration_type,
            "frequency_days": c.frequency_days,
            "last_calibration": c.last_calibration,
            "next_calibration": c.next_calibration,
            "remarks": c.remarks,
            "inventory_item_id": c.inventory_item.id,
            "created_at": c.created_at,
            "updated_at": c.updated_at,
            "created_by": c.created_by.id
        }
        for c in calibrations
    ]

@router.get("/calibrations/{calibration_id}", response_model=CalibrationScheduleResponse)
@db_session
def get_calibration(calibration_id: int):
    calibration = CalibrationSchedule.get(id=calibration_id)
    if not calibration:
        raise HTTPException(status_code=404, detail="Calibration schedule not found")
    return {
        "id": calibration.id,
        "calibration_type": calibration.calibration_type,
        "frequency_days": calibration.frequency_days,
        "last_calibration": calibration.last_calibration,
        "next_calibration": calibration.next_calibration,
        "remarks": calibration.remarks,
        "inventory_item_id": calibration.inventory_item.id,
        "created_at": calibration.created_at,
        "updated_at": calibration.updated_at,
        "created_by": calibration.created_by.id
    }

@router.put("/calibrations/{calibration_id}", response_model=CalibrationScheduleResponse)
@db_session
def update_calibration(calibration_id: int, calibration: CalibrationScheduleUpdate):
    db_calibration = CalibrationSchedule.get(id=calibration_id)
    if not db_calibration:
        raise HTTPException(status_code=404, detail="Calibration schedule not found")
    
    if calibration.calibration_type is not None:
        db_calibration.calibration_type = calibration.calibration_type
    if calibration.frequency_days is not None:
        db_calibration.frequency_days = calibration.frequency_days
    if calibration.last_calibration is not None:
        db_calibration.last_calibration = calibration.last_calibration
    if calibration.next_calibration is not None:
        db_calibration.next_calibration = calibration.next_calibration
    if calibration.remarks is not None:
        db_calibration.remarks = calibration.remarks
    
    db_calibration.updated_at = datetime.utcnow()
    commit()

    return {
        "id": db_calibration.id,
        "calibration_type": db_calibration.calibration_type,
        "frequency_days": db_calibration.frequency_days,
        "last_calibration": db_calibration.last_calibration,
        "next_calibration": db_calibration.next_calibration,
        "remarks": db_calibration.remarks,
        "inventory_item_id": db_calibration.inventory_item.id,
        "created_at": db_calibration.created_at,
        "updated_at": db_calibration.updated_at,
        "created_by": db_calibration.created_by.id
    }

@router.delete("/calibrations/{calibration_id}", status_code=204)
@db_session
def delete_calibration(calibration_id: int):
    calibration = CalibrationSchedule.get(id=calibration_id)
    if not calibration:
        raise HTTPException(status_code=404, detail="Calibration schedule not found")
    calibration.delete()
    commit()

# Calibration History Endpoints
@router.post("/calibration-history/", response_model=CalibrationHistoryResponse)
@db_session
def create_calibration_history(history: CalibrationHistoryCreate):
    """
    Create a new calibration history entry.
    
    Sample request:
    ```json
    {
        "calibration_date": "2024-01-01T00:00:00Z",
        "result": "Pass",
        "certificate_number": "CAL-2024-001",
        "remarks": "All measurements within tolerance",
        "next_due_date": "2024-04-01T00:00:00Z",
        "calibration_schedule_id": 1,
        "performed_by": 1
    }
    ```
    """
    schedule = CalibrationSchedule.get(id=history.calibration_schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="Calibration schedule not found")
    
    performer = User.get(id=history.performed_by)
    if not performer:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Validate that next_due_date is after calibration_date
    if history.next_due_date <= history.calibration_date:
        raise HTTPException(
            status_code=400,
            detail="Next due date must be after calibration date"
        )
    
    new_history = CalibrationHistory(
        calibration_schedule=schedule,
        calibration_date=history.calibration_date,
        result=history.result.value,
        certificate_number=history.certificate_number,
        remarks=history.remarks,
        next_due_date=history.next_due_date,
        performed_by=performer,
        created_at=datetime.utcnow()
    )
    commit()
    
    response_data = {
        "id": new_history.id,
        "calibration_schedule_id": schedule.id,
        "calibration_date": new_history.calibration_date,
        "result": new_history.result,
        "certificate_number": new_history.certificate_number,
        "remarks": new_history.remarks,
        "next_due_date": new_history.next_due_date,
        "performed_by": performer.id,
        "created_at": new_history.created_at
    }
    return response_data

@router.get("/calibration-history/", response_model=List[CalibrationHistoryResponse])
@db_session
def get_all_calibration_history():
    histories = select(h for h in CalibrationHistory)[:]
    return [
        {
            "id": h.id,
            "calibration_schedule_id": h.calibration_schedule.id,
            "calibration_date": h.calibration_date,
            "result": h.result,
            "certificate_number": h.certificate_number,
            "remarks": h.remarks,
            "next_due_date": h.next_due_date,
            "performed_by": h.performed_by.id,
            "created_at": h.created_at
        }
        for h in histories
    ]

@router.get("/calibration-history/{history_id}", response_model=CalibrationHistoryResponse)
@db_session
def get_calibration_history(history_id: int):
    history = CalibrationHistory.get(id=history_id)
    if not history:
        raise HTTPException(status_code=404, detail="Calibration history not found")
    return {
        "id": history.id,
        "calibration_schedule_id": history.calibration_schedule.id,
        "calibration_date": history.calibration_date,
        "result": history.result,
        "certificate_number": history.certificate_number,
        "remarks": history.remarks,
        "next_due_date": history.next_due_date,
        "performed_by": history.performed_by.id,
        "created_at": history.created_at
    }

# Inventory Request Endpoints
@router.get("/requests/", response_model=List[InventoryRequestResponse])
@db_session
def get_all_requests():
    requests = select(r for r in InventoryRequest)[:]
    return [
        {
            "id": r.id,
            "inventory_item_id": r.inventory_item.id,
            "requested_by": r.requested_by.id,
            "order_id": r.order.id,
            "operation_id": r.operation.id if r.operation else None,
            "quantity": r.quantity,
            "purpose": r.purpose,
            "status": r.status,
            "approved_by": r.approved_by.id if r.approved_by else None,
            "approved_at": r.approved_at,
            "expected_return_date": r.expected_return_date,
            "actual_return_date": r.actual_return_date,
            "remarks": r.remarks,
            "created_at": r.created_at,
            "updated_at": r.updated_at
        }
        for r in requests
    ]

@router.get("/requests/{request_id}", response_model=InventoryRequestResponse)
@db_session
def get_request(request_id: int):
    request = InventoryRequest.get(id=request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    return {
        "id": request.id,
        "inventory_item_id": request.inventory_item.id,
        "requested_by": request.requested_by.id,
        "order_id": request.order.id,
        "operation_id": request.operation.id if request.operation else None,
        "quantity": request.quantity,
        "purpose": request.purpose,
        "status": request.status,
        "approved_by": request.approved_by.id if request.approved_by else None,
        "approved_at": request.approved_at,
        "expected_return_date": request.expected_return_date,
        "actual_return_date": request.actual_return_date,
        "remarks": request.remarks,
        "created_at": request.created_at,
        "updated_at": request.updated_at
    }

@router.put("/requests/{request_id}", response_model=InventoryRequestResponse)
@db_session
def update_request(request_id: int, request: InventoryRequestUpdate):
    db_request = InventoryRequest.get(id=request_id)
    if not db_request:
        raise HTTPException(status_code=404, detail="Request not found")
    
    if request.quantity is not None:
        db_request.quantity = request.quantity
    if request.purpose is not None:
        db_request.purpose = request.purpose
    if request.status is not None:
        db_request.status = request.status.value
    if request.expected_return_date is not None:
        db_request.expected_return_date = request.expected_return_date
    if request.actual_return_date is not None:
        db_request.actual_return_date = request.actual_return_date
    if request.remarks is not None:
        db_request.remarks = request.remarks
    if request.approved_by is not None:
        approver = User.get(id=request.approved_by)
        if not approver:
            raise HTTPException(status_code=404, detail="Approver not found")
        db_request.approved_by = approver
    if request.approved_at is not None:
        db_request.approved_at = request.approved_at
    
    db_request.updated_at = datetime.utcnow()
    commit()
    return db_request.to_dict()

# Inventory Transaction Endpoints
@router.get("/transactions/", response_model=List[InventoryTransactionResponse])
@db_session
def get_all_transactions():
    transactions = select(t for t in InventoryTransaction)[:]
    return [
        {
            "id": t.id,
            "inventory_item_id": t.inventory_item.id,
            "transaction_type": t.transaction_type,
            "quantity": t.quantity,
            "reference_request_id": t.reference_request.id if t.reference_request else None,
            "performed_by": t.performed_by.id,
            "remarks": t.remarks,
            "created_at": t.created_at
        }
        for t in transactions
    ]

@router.get("/transactions/{transaction_id}", response_model=InventoryTransactionResponse)
@db_session
def get_transaction(transaction_id: int):
    transaction = InventoryTransaction.get(id=transaction_id)
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return {
        "id": transaction.id,
        "inventory_item_id": transaction.inventory_item.id,
        "transaction_type": transaction.transaction_type,
        "quantity": transaction.quantity,
        "reference_request_id": transaction.reference_request.id if transaction.reference_request else None,
        "performed_by": transaction.performed_by.id,
        "remarks": transaction.remarks,
        "created_at": transaction.created_at
    }

# Analytics Endpoints
@router.get("/analytics/items-by-status", response_model=List[StatusCount])
@db_session
def get_items_by_status():
    status_counts = {}
    for item in InventoryItem.select():
        status = item.status
        status_counts[status] = status_counts.get(status, 0) + 1
    return [{"status": k, "count": v} for k, v in status_counts.items()]

@router.get("/analytics/requests-by-status", response_model=List[StatusCount])
@db_session
def get_requests_by_status():
    status_counts = {}
    for req in InventoryRequest.select():
        status = req.status
        status_counts[status] = status_counts.get(status, 0) + 1
    return [{"status": k, "count": v} for k, v in status_counts.items()]

@router.get("/analytics/upcoming-calibrations", response_model=List[CalibrationDue])
@db_session
def get_upcoming_calibrations(days: int = 7):
    cutoff_date = datetime.utcnow() + timedelta(days=days)
    calibrations = select(
        c for c in CalibrationSchedule 
        if c.next_calibration <= cutoff_date
    )[:]
    
    return [
        {
            "item_id": c.inventory_item.id,
            "item_code": c.inventory_item.item_code,
            "next_calibration": c.next_calibration
        }
        for c in calibrations
    ]

@router.get("/analytics/transaction-summary", response_model=List[TransactionSummary])
@db_session
def get_transaction_summary():
    summary = {}
    for t in InventoryTransaction.select():
        t_type = t.transaction_type
        summary[t_type] = summary.get(t_type, 0) + t.quantity
    return [{"transaction_type": k, "total_quantity": v} for k, v in summary.items()]

# Add this new endpoint after the existing subcategories endpoints
@router.get("/categories/{category_id}/subcategories", response_model=List[InventorySubCategoryResponse])
@db_session
def get_subcategories_by_category(category_id: int):
    """
    Get all subcategories for a specific category.
    
    Parameters:
    - category_id: ID of the category to get subcategories for
    
    Returns a list of subcategories belonging to the specified category.
    If the category doesn't exist, returns a 404 error.
    """
    category = InventoryCategory.get(id=category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    subcategories = select(s for s in InventorySubCategory if s.category.id == category_id)[:]
    return [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "dynamic_fields": s.dynamic_fields,
            "category_id": s.category.id,
            "created_at": s.created_at,
            "created_by": s.created_by.id
        }
        for s in subcategories
    ]