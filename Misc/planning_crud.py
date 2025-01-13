from sqlalchemy.orm import Session, joinedload
from Database.db_setup import SessionLocal
from orm_class.master_order import Order, Project
from pydantic_schema.request_body import OrderUpdate, OrderCreate
from fastapi import HTTPException
from datetime import datetime
from sqlalchemy.exc import IntegrityError
from typing import Optional

async def getAllOrders():
    db = SessionLocal()
    try:
        # Query orders with relationships loaded
        orders = db.query(Order).options(
            joinedload(Order.project),
            joinedload(Order.raw_materials)
        )
        





        
        return orders
    except Exception as e:
        raise e
    finally:
        db.close()

async def update_order(order_number: str, update_data: OrderUpdate) -> Order:
    db = SessionLocal()
    try:
        # Get the order with its project
        order = db.query(Order).options(
            joinedload(Order.project),
            joinedload(Order.raw_materials)
        ).filter(Order.production_order == order_number).first()
        
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        
        # Define allowed fields for update
        allowed_fields = {
            'sale_order', 'wbs_element', 'part_number', 
            'part_description', 'total_operations', 
            'required_quantity', 'launched_quantity', 
            'plant_id'
        }
        
        # Update order fields if provided
        update_dict = update_data.dict(exclude_unset=True)
        delivery_date = update_dict.pop('delivery_date', None)
        
        # Update only allowed fields
        for key, value in update_dict.items():
            if key in allowed_fields and value is not None:
                setattr(order, key, value)
        
        # Update project delivery date if provided
        if delivery_date is not None and order.project:
            # delivery_date is already converted to datetime by the pydantic validator
            order.project.delivery_date = delivery_date
        
        db.commit()
        db.refresh(order)
        return order
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

async def create_order(order_data: OrderCreate, session: Session) -> Optional[Order]:
    try:
        # Check if order with same production_order exists
        existing_order = session.query(Order).filter(
            Order.production_order == order_data.production_order
        ).first()
        
        if existing_order:
            raise HTTPException(
                status_code=409,
                detail=f"Order with production order {order_data.production_order} already exists"
            )

        # Get or create project
        project = session.query(Project).filter(
            Project.name == order_data.project_name
        ).first()

        if not project:
            project = Project(
                name=order_data.project_name,
                priority=1,  # Default priority
                delivery_date=order_data.delivery_date
            )
            try:
                session.add(project)
                session.flush()
            except IntegrityError:
                session.rollback()
                raise HTTPException(
                    status_code=400,
                    detail=f"Error creating project: {order_data.project_name}"
                )

        # Create new order
        new_order = Order(
            production_order=order_data.production_order,
            sale_order=order_data.sale_order,
            wbs_element=order_data.wbs_element,
            part_number=order_data.part_number,
            part_description=order_data.part_description,
            total_operations=order_data.total_operations,
            required_quantity=order_data.required_quantity,
            launched_quantity=order_data.launched_quantity,
            plant_id=order_data.plant_id,
            project_id=project.id
        )

        try:
            session.add(new_order)
            session.commit()
            session.refresh(new_order)
            return new_order
        except IntegrityError as e:
            session.rollback()
            raise HTTPException(
                status_code=400,
                detail=f"Database integrity error: {str(e)}"
            )
        except Exception as e:
            session.rollback()
            raise HTTPException(
                status_code=500,
                detail=f"Error creating order: {str(e)}"
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )
