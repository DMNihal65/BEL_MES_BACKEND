# endpoints.py
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import List, Optional
from datetime import datetime, timedelta, timezone
from pony.orm import db_session, commit, select, flush, rollback, TransactionIntegrityError, desc, DatabaseError
from datetime import datetime

from pony.utils import count

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
                                     BulkInventoryItemCreate,
                                     TransactionType, InventoryRequestStatus,
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
from app.models.master_order import Order, Operation
from pony.orm import desc

router = APIRouter(prefix="/api/v1/inventory", tags=["inventory"])


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
        print("Returning response data:", response_data)
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

        # Validate quantities in each item
        for item_data in bulk_items.items:
            if item_data['available_quantity'] > item_data['quantity']:
                raise HTTPException(
                    status_code=400,
                    detail=f"Available quantity cannot be greater than total quantity for item {item_data['item_code']}"
                )

        # Create new items
        new_items = []
        for item_data in bulk_items.items:
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
            new_items.append(new_item)

        # Commit the transaction
        commit()

        # Fetch the created items to ensure all data is correctly retrieved
        created_items = select(i for i in InventoryItem if i in new_items)[:]

        # Convert to the response model
        response = [InventoryItemResponse(
            id=item.id,
            item_code=item.item_code,
            dynamic_data=item.dynamic_data,
            quantity=item.quantity,
            available_quantity=item.available_quantity,
            status=item.status,
            subcategory_id=item.subcategory.id,
            created_at=item.created_at,
            updated_at=item.updated_at,
            created_by=item.created_by.id
        ) for item in created_items]

        return response

    except HTTPException as he:
        rollback()
        raise he
    except DatabaseError as db_error:
        rollback()
        if "duplicate key value violates unique constraint" in str(db_error):
            raise HTTPException(
                status_code=400,
                detail="One or more item codes already exist in the database"
            )
        raise HTTPException(
            status_code=400,
            detail=f"Database error occurred: {str(db_error)}"
        )
    except Exception as e:
        rollback()
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred: {str(e)}"
        )


# # Calibration Schedule Endpoints
# @router.post("/calibrations/", response_model=CalibrationScheduleResponse)
# @db_session
# def create_calibration_schedule(calibration: CalibrationScheduleCreate):
#     """
#     Create a new calibration schedule.
#
#     Sample request:
#     ```json
#     {
#         "calibration_type": "Dimensional",
#         "frequency_days": 90,
#         "last_calibration": "2024-01-01T00:00:00Z",
#         "next_calibration": "2024-04-01T00:00:00Z",
#         "remarks": "Regular calibration schedule",
#         "inventory_item_id": 1,
#         "created_by": 1
#     }
#     ```
#     """
#     item = InventoryItem.get(id=calibration.inventory_item_id)
#     if not item:
#         raise HTTPException(status_code=404, detail="Inventory item not found")
#
#     user = User.get(id=calibration.created_by)
#     if not user:
#         raise HTTPException(status_code=404, detail="User not found")
#
#     # Validate that next_calibration is after last_calibration
#     if calibration.last_calibration and calibration.next_calibration <= calibration.last_calibration:
#         raise HTTPException(
#             status_code=400,
#             detail="Next calibration date must be after last calibration date"
#         )
#
#     new_calibration = CalibrationSchedule(
#         inventory_item=item,
#         calibration_type=calibration.calibration_type,
#         frequency_days=calibration.frequency_days,
#         last_calibration=calibration.last_calibration,
#         next_calibration=calibration.next_calibration,
#         remarks=calibration.remarks,
#         created_by=user,
#         created_at=datetime.utcnow(),
#         updated_at=datetime.utcnow()
#     )
#     commit()
#
#     response_data = {
#         "id": new_calibration.id,
#         "calibration_type": new_calibration.calibration_type,
#         "frequency_days": new_calibration.frequency_days,
#         "last_calibration": new_calibration.last_calibration,
#         "next_calibration": new_calibration.next_calibration,
#         "remarks": new_calibration.remarks,
#         "inventory_item_id": item.id,
#         "created_at": new_calibration.created_at,
#         "updated_at": new_calibration.updated_at,
#         "created_by": user.id
#     }
#     return response_data


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
    try:
        # Validate inventory item exists
        item = InventoryItem.get(id=calibration.inventory_item_id)
        if not item:
            raise HTTPException(status_code=404, detail="Inventory item not found")

        # Validate user exists
        user = User.get(id=calibration.created_by)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Check for duplicate calibration schedule
        existing_calibration = CalibrationSchedule.get(
            inventory_item=item,
            calibration_type=calibration.calibration_type
        )
        if existing_calibration:
            raise HTTPException(
                status_code=409,
                detail=f"Calibration schedule for {calibration.calibration_type} already exists for this item. Please update the calibration date in the calibration page."
            )

        # Validate that next_calibration is after last_calibration
        if calibration.last_calibration and calibration.next_calibration <= calibration.last_calibration:
            raise HTTPException(
                status_code=400,
                detail="Next calibration date must be after last calibration date"
            )

        # Validate frequency_days is positive
        if calibration.frequency_days <= 0:
            raise HTTPException(
                status_code=400,
                detail="Frequency days must be a positive number"
            )

        # Validate calibration_type is not empty
        if not calibration.calibration_type or calibration.calibration_type.strip() == "":
            raise HTTPException(
                status_code=400,
                detail="Calibration type cannot be empty"
            )

        # Validate dates are not in the past (optional - uncomment if needed)
        # current_date = datetime.utcnow().date()
        # if calibration.next_calibration.date() < current_date:
        #     raise HTTPException(
        #         status_code=400,
        #         detail="Next calibration date cannot be in the past"
        #     )

        # Create new calibration schedule
        new_calibration = CalibrationSchedule(
            inventory_item=item,
            calibration_type=calibration.calibration_type.strip(),
            frequency_days=calibration.frequency_days,
            last_calibration=calibration.last_calibration,
            next_calibration=calibration.next_calibration,
            remarks=calibration.remarks.strip() if calibration.remarks else None,
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

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        # Handle any unexpected errors
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while creating the calibration schedule: {str(e)}"
        )

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
        commit()

        response_data = {
            "id": new_request.id,
            "inventory_item_id": item.id,
            "inventory_item_code": new_request.inventory_item.item_code,
            "requested_by": current_user.id,
            "requested_by_username": current_user.username,
            "order_id": order.id,
            "operation_id": operation.id if operation else None,
            "quantity": new_request.quantity,
            "purpose": new_request.purpose,
            "status": new_request.status if isinstance(new_request.status, str) else new_request.status.value,
            "expected_return_date": new_request.expected_return_date,
            "actual_return_date": None,
            "remarks": new_request.remarks,
            "approved_by_username": None,
            "created_at": new_request.created_at,
            "updated_at": new_request.updated_at,
            "approved_by": None,
            "approved_at": None
        }

        print("Returning response data:", response_data)
        return response_data

    except HTTPException as he:
        rollback()
        raise he
    except Exception as e:
        rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/transactions/", response_model=InventoryTransactionResponse)
def create_transaction(
        transaction: InventoryTransactionCreate,
        current_user: User = Depends(get_current_user)
):
    """
    Create a new inventory transaction and update the reference request status if provided.
    """
    try:
        with db_session:
            # Get all required data within the same session
            item_id = transaction.inventory_item_id
            user_id = current_user.id
            transaction_type = transaction.transaction_type
            quantity = transaction.quantity
            remarks = transaction.remarks
            request_id = transaction.reference_request_id

            # Get the inventory item
            item = InventoryItem.get(id=item_id)
            if not item:
                raise HTTPException(status_code=404, detail="Inventory item not found")

            # Get user in current session
            user = User.get(id=user_id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # Get reference request if provided
            request = None
            if request_id:
                request = InventoryRequest.get(id=request_id)
                if not request:
                    raise HTTPException(status_code=404, detail="Reference request not found")

            # Validate transaction quantity based on type
            if transaction_type == TransactionType.ISSUE:
                if quantity > item.available_quantity:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Issue quantity ({quantity}) exceeds available quantity ({item.available_quantity})"
                    )
                item.available_quantity -= quantity
            elif transaction_type == TransactionType.RETURN:
                max_returnable = item.quantity - item.available_quantity
                if quantity > max_returnable:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Return quantity exceeds issued quantity. Maximum returnable: {max_returnable}"
                    )
                item.available_quantity += quantity

            current_time = datetime.now(timezone.utc)

            # Create transaction record
            new_transaction = InventoryTransaction(
                inventory_item=item,
                transaction_type=transaction_type.value,
                quantity=quantity,
                reference_request=request,
                performed_by=user,
                remarks=remarks,
                created_at=current_time
            )

            # Update item timestamp
            item.updated_at = current_time

            # Flush changes to get IDs
            flush()

            # Update reference request if provided
            if request:
                request.status = InventoryRequestStatus.APPROVED.value  # Use .value for string
                request.approved_at = current_time
                request.approved_by = user
                request.updated_at = current_time  # Update the updated_at field

            # Commit changes
            commit()

            # Create response data without accessing database objects
            response_data = {
                "id": new_transaction.id,
                "inventory_item_id": item_id,
                "transaction_type": transaction_type.value,
                "quantity": quantity,
                "reference_request_id": request_id,
                "performed_by": user_id,
                "performed_by_username": user.username,
                "remarks": remarks,
                "created_at": current_time
            }

            return response_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/transactions/by-item/{item_id}", response_model=List[InventoryTransactionResponse])
@db_session
def get_item_transactions(
        item_id: int,
        transaction_type: Optional[str] = Query(None, enum=[t.value for t in TransactionType]),
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = Query(20, gt=0, le=100)
):
    """
    Get transaction history for a specific inventory item with optional filters.
    """
    item = InventoryItem.get(id=item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Inventory item not found")

    # Build query
    query = select(t for t in InventoryTransaction if t.inventory_item.id == item_id)

    if transaction_type:
        query = query.filter(lambda t: t.transaction_type == transaction_type)
    if start_date:
        query = query.filter(lambda t: t.created_at >= start_date)
    if end_date:
        query = query.filter(lambda t: t.created_at <= end_date)

    transactions = query.order_by(desc(InventoryTransaction.created_at)).limit(limit)[:]

    return [
        {
            "id": t.id,
            "inventory_item_id": t.inventory_item.id,
            "transaction_type": t.transaction_type,
            "quantity": t.quantity,
            "reference_request_id": t.reference_request.id if t.reference_request else None,
            "performed_by": t.performed_by.id,
            "performed_by_username": t.performed_by.username,
            "remarks": t.remarks,
            "created_at": t.created_at
        }
        for t in transactions
    ]


@router.post("/transactions/bulk-return/", response_model=List[InventoryTransactionResponse])
@db_session
def bulk_return_items(
        request_ids: List[int],
        current_user: User = Depends(get_current_user)
):
    """
    Process bulk returns for multiple inventory requests.
    """
    transactions = []

    try:
        for request_id in request_ids:
            request = InventoryRequest.get(id=request_id)
            if not request:
                raise HTTPException(
                    status_code=404,
                    detail=f"Request {request_id} not found"
                )

            if request.status != "Issued":
                raise HTTPException(
                    status_code=400,
                    detail=f"Request {request_id} is not in 'Issued' status"
                )

            # Create return transaction
            new_transaction = InventoryTransaction(
                inventory_item=request.inventory_item,
                transaction_type=TransactionType.RETURN.value,
                quantity=request.quantity,
                reference_request=request,
                performed_by=current_user,
                remarks=f"Bulk return for request {request_id}",
                created_at=datetime.now(timezone.utc)
            )

            # Update inventory item
            request.inventory_item.available_quantity += request.quantity
            request.inventory_item.updated_at = datetime.now(timezone.utc)

            # Update request status
            request.status = "Returned"
            request.actual_return_date = datetime.now(timezone.utc)
            request.updated_at = datetime.now(timezone.utc)

            transactions.append(new_transaction)

        commit()

        return [
            {
                "id": t.id,
                "inventory_item_id": t.inventory_item.id,
                "transaction_type": t.transaction_type,
                "quantity": t.quantity,
                "reference_request_id": t.reference_request.id,
                "performed_by": current_user.id,
                "performed_by_username": current_user.username,
                "remarks": t.remarks,
                "created_at": t.created_at
            }
            for t in transactions
        ]

    except Exception as e:
        rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/transactions/summary/daily", response_model=List[TransactionSummary])
@db_session
def get_daily_transaction_summary(
        start_date: datetime,
        end_date: Optional[datetime] = None
):
    """
    Get daily transaction summary within a date range.
    """
    if not end_date:
        end_date = datetime.now(timezone.utc)

    transactions = select(
        (t.transaction_type, sum(t.quantity))
        for t in InventoryTransaction
        if t.created_at >= start_date and t.created_at <= end_date
    )[:]

    return [
        {
            "transaction_type": t_type,
            "total_quantity": quantity
        }
        for t_type, quantity in transactions
    ]


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
    try:
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
        # Return only serializable fields
        return {
            "id": db_subcategory.id,
            "name": db_subcategory.name,
            "description": db_subcategory.description,
            "dynamic_fields": db_subcategory.dynamic_fields,
            "category_id": db_subcategory.category.id,
            "created_at": db_subcategory.created_at,
            "created_by": db_subcategory.created_by.id
        }
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


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
        "performed_by_username": performer.username,
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
            "performed_by_username": h.performed_by.username,
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
        "performed_by_username": history.performed_by.username,
        "created_at": history.created_at
    }


# Inventory Request Endpoints
@router.get("/requests/", response_model=List[InventoryRequestResponse])
@db_session
def get_all_requests():
    """
    Get all inventory requests.
    """
    try:
        requests = select(r for r in InventoryRequest)[:]

        # Map any 'Issued' status to 'Approved' for response validation
        response_data = []
        for r in requests:
            # Convert status to match enum values if needed
            status = r.status
            if status == "Issued":
                status = "Approved"  # Map 'Issued' to 'Approved'

            response_data.append({
                "id": r.id,
                "inventory_item_id": r.inventory_item.id,
                "inventory_item_code": r.inventory_item.item_code,
                "requested_by": r.requested_by.id,
                "requested_by_username": r.requested_by.username,
                "order_id": r.order.id,
                "operation_id": r.operation.id if r.operation else None,
                "quantity": r.quantity,
                "purpose": r.purpose,
                "status": status,  # Use the mapped status
                "approved_by": r.approved_by.id if r.approved_by else None,
                "approved_by_username": r.approved_by.username if r.approved_by else None,
                "approved_at": r.approved_at,
                "expected_return_date": r.expected_return_date,
                "actual_return_date": r.actual_return_date,
                "remarks": r.remarks,
                "created_at": r.created_at,
                "updated_at": r.updated_at
            })
        print(response_data)

        return response_data



    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        "requested_by_username": request.requested_by.username,
        "inventory_item_code": request.inventory_item.item_code,
        "order_id": request.order.id,
        "operation_id": request.operation.id if request.operation else None,
        "quantity": request.quantity,
        "purpose": request.purpose,
        "status": request.status,
        "approved_by": request.approved_by.id if request.approved_by else None,
        "approved_by_username": request.approved_by.username if request.approved_by else None,
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


# Add these new analytics endpoints
@router.get("/analytics/transaction-metrics", response_model=dict)
@db_session
def get_transaction_metrics(
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
):
    """
    Get comprehensive transaction metrics including:
    - Total transactions by type
    - Most active items
    - Transaction trends
    - Request fulfillment rates
    """
    try:
        if not end_date:
            end_date = datetime.now(timezone.utc)
        if not start_date:
            start_date = end_date - timedelta(days=30)  # Default to last 30 days

        # Get all transactions in date range
        transactions = select(t for t in InventoryTransaction
                              if t.created_at >= start_date and t.created_at <= end_date)[:]

        # Initialize metrics
        metrics = {
            "total_transactions": len(transactions),
            "transaction_by_type": {},
            "total_items_issued": 0,
            "total_items_returned": 0,
            "most_active_items": [],
            "daily_transaction_counts": {},
            "average_time_to_return": None,
            "pending_returns": 0,
            "request_fulfillment_rate": 0,
            "top_requesters": []
        }

        # Calculate transaction type counts and quantities
        item_transaction_counts = {}
        for t in transactions:
            # Count by transaction type
            metrics["transaction_by_type"][t.transaction_type] = metrics["transaction_by_type"].get(t.transaction_type,
                                                                                                    0) + 1

            # Track quantities by type
            if t.transaction_type == "Issue":
                metrics["total_items_issued"] += t.quantity
            elif t.transaction_type == "Return":
                metrics["total_items_returned"] += t.quantity

            # Count transactions by item
            item_id = t.inventory_item.id
            item_transaction_counts[item_id] = item_transaction_counts.get(item_id, 0) + 1

            # Track daily counts
            date_key = t.created_at.date().isoformat()
            metrics["daily_transaction_counts"][date_key] = metrics["daily_transaction_counts"].get(date_key, 0) + 1

        # Get most active items (top 5)
        most_active_items = sorted(item_transaction_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        metrics["most_active_items"] = [
            {
                "item_id": item_id,
                "item_code": InventoryItem[item_id].item_code,
                "item_subcategory": InventoryItem[item_id].subcategory.name,
                "item_category": InventoryItem[item_id].subcategory.category.name,
                "item_details": InventoryItem[item_id].dynamic_data,
                "transaction_count": count
            }
            for item_id, count in most_active_items
        ]

        # Calculate request metrics
        requests = select(r for r in InventoryRequest
                          if r.created_at >= start_date and r.created_at <= end_date)[:]

        total_requests = len(requests)
        fulfilled_requests = sum(1 for r in requests if r.status in ["Approved", "Issued"])

        if total_requests > 0:
            metrics["request_fulfillment_rate"] = (fulfilled_requests / total_requests) * 100

        # Calculate average return time
        return_times = []
        pending_returns = 0
        for req in requests:
            if req.status == "Issued":
                pending_returns += 1
            elif req.status == "Returned" and req.actual_return_date:
                return_time = (req.actual_return_date - req.created_at).total_seconds() / 3600  # hours
                return_times.append(return_time)

        metrics["pending_returns"] = pending_returns
        if return_times:
            metrics["average_time_to_return"] = sum(return_times) / len(return_times)

        # Get top requesters
        requester_counts = {}
        for req in requests:
            requester_id = req.requested_by.id
            requester_counts[requester_id] = requester_counts.get(requester_id, 0) + 1

        top_requesters = sorted(requester_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        metrics["top_requesters"] = [
            {
                "user_id": user_id,
                "request_count": count,
                "user_name": User[user_id].username
            }
            for user_id, count in top_requesters
        ]

        return metrics

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analytics/inventory-utilization", response_model=dict)
@db_session
def get_inventory_utilization(time_period: Optional[int] = 30):
    """
    Get inventory utilization metrics
    """
    try:
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=time_period)

        metrics = {
            "high_demand_items": [],
            "low_utilization_items": [],
            "utilization_by_category": {},
            "stock_turnover_rate": {},
            "critical_stock_items": []
        }

        # Calculate utilization for each item
        items = select(i for i in InventoryItem)[:]

        for item in items:
            # Get transactions for this item
            transactions = select(t for t in InventoryTransaction
                                  if t.inventory_item == item and
                                  t.created_at >= start_date and
                                  t.created_at <= end_date)[:]

            total_issued = sum(t.quantity for t in transactions if t.transaction_type == TransactionType.ISSUE.value)
            utilization_rate = (total_issued / item.quantity * 100) if item.quantity > 0 else 0

            # Track by category
            category_name = item.subcategory.category.name
            if category_name not in metrics["utilization_by_category"]:
                metrics["utilization_by_category"][category_name] = {
                    "total_items": 0,
                    "total_utilization": 0
                }

            metrics["utilization_by_category"][category_name]["total_items"] += 1
            metrics["utilization_by_category"][category_name]["total_utilization"] += utilization_rate

            # Identify high demand items (>70% utilization)
            if utilization_rate > 70:
                metrics["high_demand_items"].append({
                    "item_id": item.id,
                    "item_code": item.item_code,
                    "utilization_rate": round(utilization_rate, 2),
                    "available_quantity": item.available_quantity,
                    "category": category_name
                })

            # Identify low utilization items (<30% utilization)
            if utilization_rate < 30:
                metrics["low_utilization_items"].append({
                    "item_id": item.id,
                    "item_code": item.item_code,
                    "utilization_rate": round(utilization_rate, 2),
                    "quantity": item.quantity,
                    "category": category_name
                })

            # Calculate stock turnover rate
            if item.quantity > 0:
                turnover_rate = total_issued / item.quantity
                metrics["stock_turnover_rate"][item.item_code] = round(turnover_rate, 2)

            # Identify critical stock items (less than 20% available)
            if item.quantity > 0 and (item.available_quantity / item.quantity) < 0.2:
                metrics["critical_stock_items"].append({
                    "item_id": item.id,
                    "item_code": item.item_code,
                    "available_quantity": item.available_quantity,
                    "total_quantity": item.quantity,
                    "category": category_name,
                    "percentage_available": round((item.available_quantity / item.quantity) * 100, 2)
                })

        # Calculate average utilization by category
        for category in metrics["utilization_by_category"]:
            total_items = metrics["utilization_by_category"][category]["total_items"]
            if total_items > 0:
                metrics["utilization_by_category"][category]["average_utilization"] = round(
                    metrics["utilization_by_category"][category]["total_utilization"] / total_items,
                    2
                )
            # Add item count to the output
            metrics["utilization_by_category"][category]["item_count"] = total_items

        # Add summary statistics
        metrics["summary"] = {
            "total_items": len(items),
            "high_demand_count": len(metrics["high_demand_items"]),
            "low_utilization_count": len(metrics["low_utilization_items"]),
            "critical_stock_count": len(metrics["critical_stock_items"]),
            "categories_count": len(metrics["utilization_by_category"]),
            "time_period_days": time_period
        }

        return metrics

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analytics/transaction-summary", response_model=dict)
@db_session
def get_transaction_summary(
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        category_id: Optional[int] = None
):
    """
    Get transaction summary with detailed metrics
    """
    try:
        if not end_date:
            end_date = datetime.now(timezone.utc)
        if not start_date:
            start_date = end_date - timedelta(days=30)

        # Base query for transactions
        query = lambda: select(t for t in InventoryTransaction
                               if t.created_at >= start_date and
                               t.created_at <= end_date)

        # Add category filter if specified
        if category_id:
            query = lambda: select(t for t in InventoryTransaction
                                   if t.created_at >= start_date and
                                   t.created_at <= end_date and
                                   t.inventory_item.subcategory.category.id == category_id)

        transactions = query()[:]

        summary = {
            "total_transactions": len(transactions),
            "transactions_by_type": {},
            "daily_transactions": {},
            "items_summary": {},
            "category_summary": {},
            "time_metrics": {
                "start_date": start_date,
                "end_date": end_date,
                "period_days": (end_date - start_date).days
            }
        }

        # Calculate transactions by type and daily transactions
        for t in transactions:
            # By type
            t_type = t.transaction_type
            if t_type not in summary["transactions_by_type"]:
                summary["transactions_by_type"][t_type] = {
                    "count": 0,
                    "total_quantity": 0
                }
            summary["transactions_by_type"][t_type]["count"] += 1
            summary["transactions_by_type"][t_type]["total_quantity"] += t.quantity

            # Daily transactions
            date_key = t.created_at.date().isoformat()
            if date_key not in summary["daily_transactions"]:
                summary["daily_transactions"][date_key] = {
                    "total": 0,
                    "by_type": {}
                }
            summary["daily_transactions"][date_key]["total"] += 1
            if t_type not in summary["daily_transactions"][date_key]["by_type"]:
                summary["daily_transactions"][date_key]["by_type"][t_type] = 0
            summary["daily_transactions"][date_key]["by_type"][t_type] += 1

            # Items summary
            item_id = t.inventory_item.id
            if item_id not in summary["items_summary"]:
                summary["items_summary"][item_id] = {
                    "item_code": t.inventory_item.item_code,
                    "transaction_count": 0,
                    "total_quantity": 0
                }
            summary["items_summary"][item_id]["transaction_count"] += 1
            summary["items_summary"][item_id]["total_quantity"] += t.quantity

            # Category summary
            category = t.inventory_item.subcategory.category
            cat_id = category.id
            if cat_id not in summary["category_summary"]:
                summary["category_summary"][cat_id] = {
                    "name": category.name,
                    "transaction_count": 0,
                    "total_quantity": 0
                }
            summary["category_summary"][cat_id]["transaction_count"] += 1
            summary["category_summary"][cat_id]["total_quantity"] += t.quantity

        # Calculate averages and sort summaries
        summary["daily_average"] = round(len(transactions) / max(1, (end_date - start_date).days), 2)

        # Sort items by transaction count
        summary["top_items"] = sorted(
            [{"item_id": k, **v} for k, v in summary["items_summary"].items()],
            key=lambda x: x["transaction_count"],
            reverse=True
        )[:10]  # Top 10 items

        return summary

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analytics/transaction-history", response_model=dict)
@db_session
def get_transaction_history(
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        item_id: Optional[int] = None,
        transaction_type: Optional[TransactionType] = None,
        limit: int = 100,
        offset: int = 0
):
    """
    Get detailed transaction history with simplified item details.
    Optional filters: date range, item_id, transaction_type
    """
    try:
        if not end_date:
            end_date = datetime.now(timezone.utc)
        if not start_date:
            start_date = end_date - timedelta(days=30)

        # Build base query
        query = select(t for t in InventoryTransaction
                       if t.created_at >= start_date and
                       t.created_at <= end_date)

        # Apply filters
        if item_id:
            query = select(t for t in query if t.inventory_item.id == item_id)
        if transaction_type:
            query = select(t for t in query if t.transaction_type == transaction_type.value)

        # Get total count for pagination
        total_count = query.count()

        # Apply pagination and ordering
        transactions = query.order_by(lambda t: desc(t.created_at))[offset:offset + limit]

        # Prepare response
        response = {
            "metadata": {
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
                "filtered_count": len(transactions)
            },
            "time_range": {
                "start_date": start_date,
                "end_date": end_date
            },
            "transactions": []
        }

        for t in transactions:
            item = t.inventory_item
            performer = t.performed_by

            # Build transaction detail with simplified item info
            transaction_detail = {
                "transaction": {
                    "id": t.id,
                    "type": t.transaction_type,
                    "quantity": t.quantity,
                    "remarks": t.remarks,
                    "created_at": t.created_at,
                    "performed_by": {
                        "id": performer.id,
                        "username": performer.username
                    }
                },
                "item": {
                    "id": item.id,
                    "item_code": item.item_code,
                    "current_quantity": item.quantity,
                    "available_quantity": item.available_quantity
                }
            }

            # Add related request info if exists
            if hasattr(t, 'inventory_request') and t.inventory_request:
                request = t.inventory_request
                transaction_detail["request"] = {
                    "id": request.id,
                    "status": request.status,
                    "purpose": request.purpose,
                    "requested_by": {
                        "id": request.requested_by.id,
                        "username": request.requested_by.username
                    },
                    "expected_return_date": request.expected_return_date,
                    "actual_return_date": request.actual_return_date,
                    "order_id": request.order_id,
                    "operation_id": request.operation_id
                }
            else:
                transaction_detail["request"] = None

            response["transactions"].append(transaction_detail)

        # Add summary statistics
        response["summary"] = {
            "total_transactions": total_count,
            "transaction_types": {},
            "total_quantity_moved": 0
        }

        # Calculate summary statistics
        for t in transactions:
            t_type = t.transaction_type
            if t_type not in response["summary"]["transaction_types"]:
                response["summary"]["transaction_types"][t_type] = {
                    "count": 0,
                    "total_quantity": 0
                }
            response["summary"]["transaction_types"][t_type]["count"] += 1
            response["summary"]["transaction_types"][t_type]["total_quantity"] += t.quantity
            response["summary"]["total_quantity_moved"] += t.quantity

        return response

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analytics/transaction-history2", response_model=dict)
@db_session
def get_transaction_history(
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        item_id: Optional[int] = None,
        transaction_type: Optional[TransactionType] = None,
        limit: int = 100,
        offset: int = 0
):
    """
    Get simplified transaction history with item details for table display.
    Optional filters: date range, item_id, transaction_type
    """
    try:
        if not end_date:
            end_date = datetime.now(timezone.utc)
        if not start_date:
            start_date = end_date - timedelta(days=30)

        # Build base query with prefetch for InventoryRequest
        query = select(t for t in InventoryTransaction
                       if t.created_at >= start_date and
                       t.created_at <= end_date).prefetch(InventoryRequest)

        # Apply filters
        if item_id:
            query = select(t for t in query if t.inventory_item.id == item_id)
        if transaction_type:
            query = select(t for t in query if t.transaction_type == transaction_type.value)

        # Get total count for pagination
        total_count = count(t for t in query)

        # Apply pagination and ordering
        transactions = query.order_by(lambda t: desc(t.created_at))[offset:offset + limit]

        # Prepare simplified response
        response = {
            "metadata": {
                "total_count": total_count,
                "limit": limit,
                "offset": offset
            },
            "transactions": []
        }

        for t in transactions:
            item = t.inventory_item
            performer = t.performed_by

            transaction = {
                "id": t.id,
                "type": t.transaction_type,
                "quantity": t.quantity,
                "remarks": t.remarks,
                "created_at": t.created_at,
                "performed_by_id": performer.id,
                "performed_by_username": performer.username,
                "item_id": item.id,
                "item_code": item.item_code,
                "dynamic_data": item.dynamic_data,
                "subcategory_id": item.subcategory.id,
                "subcategory_name": item.subcategory.name,
                "category_id": item.subcategory.category.id,
                "category_name": item.subcategory.category.name,
                "current_quantity": item.quantity,
                "available_quantity": item.available_quantity,
                "request_id": t.reference_request.id if t.reference_request else None
            }

            response["transactions"].append(transaction)

        return response

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))