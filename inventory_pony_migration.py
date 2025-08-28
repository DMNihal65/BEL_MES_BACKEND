#!/usr/bin/env python3
"""
Pony ORM Migration Script for InventoryV1 Models
This script handles the migration from old to new inventory models using Pony ORM
Run this after executing the SQL migration script
"""

import os
import sys
from datetime import datetime
from decimal import Decimal

# Add the app directory to Python path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from pony.orm import *
from app.database.connection import db

def run_migration():
    """
    Main migration function that handles Pony ORM specific migrations
    """
    print("Starting Pony ORM migration for InventoryV1 models...")
    
    try:
        # Generate mapping to handle the schema changes
        # This will create/update the database schema based on the new models
        db.generate_mapping(create_tables=False)  # Don't create tables, they already exist
        
        print("✓ Database mapping generated successfully")
        
        # Verify the migration by checking if we can access the new model
        with db_session:
            # Test query to verify the new InventoryReturnRequest model works
            try:
                # This should not fail if migration was successful
                return_requests_count = count(r for r in db.InventoryReturnRequest)
                print(f"✓ InventoryReturnRequest model accessible. Current count: {return_requests_count}")
            except Exception as e:
                print(f"✗ Error accessing InventoryReturnRequest model: {e}")
                return False
            
            # Test query to verify enhanced InventoryTransaction model works
            try:
                transactions_count = count(t for t in db.InventoryTransaction)
                print(f"✓ Enhanced InventoryTransaction model accessible. Current count: {transactions_count}")
            except Exception as e:
                print(f"✗ Error accessing enhanced InventoryTransaction model: {e}")
                return False
            
            # Verify that all existing data is still accessible
            try:
                categories_count = count(c for c in db.InventoryCategory)
                subcategories_count = count(s for s in db.InventorySubCategory)
                items_count = count(i for i in db.InventoryItem)
                requests_count = count(r for r in db.InventoryRequest)
                
                print(f"✓ Data verification successful:")
                print(f"  - Categories: {categories_count}")
                print(f"  - Subcategories: {subcategories_count}")
                print(f"  - Items: {items_count}")
                print(f"  - Requests: {requests_count}")
                
            except Exception as e:
                print(f"✗ Error verifying existing data: {e}")
                return False
        
        print("✓ Pony ORM migration completed successfully!")
        return True
        
    except Exception as e:
        print(f"✗ Migration failed: {e}")
        print("Please check your database connection and ensure the SQL migration was executed first.")
        return False

def verify_model_relationships():
    """
    Verify that all model relationships are working correctly
    """
    print("\nVerifying model relationships...")
    
    try:
        with db_session:
            # Test some relationships
            for item in db.InventoryItem.select()[:5]:  # Test first 5 items
                # Test existing relationships
                subcategory = item.subcategory
                transactions = list(item.transactions)
                requests = list(item.requests)
                
                # Test new relationships
                return_requests = list(item.return_requests)
                
                print(f"✓ Item {item.item_code}: subcategory={subcategory.name if subcategory else 'None'}, "
                      f"transactions={len(transactions)}, requests={len(requests)}, "
                      f"return_requests={len(return_requests)}")
                
                break  # Just test one item to verify relationships work
            
            print("✓ Model relationships verified successfully!")
            
    except Exception as e:
        print(f"✗ Error verifying relationships: {e}")
        return False
    
    return True

def update_existing_transaction_data():
    """
    Update existing transaction records with proper quantity_before and quantity_after values
    This function attempts to calculate these values based on business logic
    """
    print("\nUpdating existing transaction data...")
    
    try:
        with db_session:
            # Get all transactions that need updating
            transactions_to_update = db.InventoryTransaction.select(
                lambda t: t.quantity_before == 0 and t.quantity_after == 0
            )
            
            updated_count = 0
            for transaction in transactions_to_update:
                try:
                    current_quantity = transaction.inventory_item.available_quantity
                    
                    # Simple logic: assume the current quantity as both before and after
                    # You may want to implement more sophisticated logic based on your business rules
                    if transaction.transaction_type.lower() in ['issue', 'maintenance']:
                        # For issue/maintenance, quantity decreases
                        transaction.quantity_before = current_quantity + transaction.quantity
                        transaction.quantity_after = current_quantity
                    elif transaction.transaction_type.lower() == 'return':
                        # For return, quantity increases
                        transaction.quantity_before = current_quantity - transaction.quantity
                        transaction.quantity_after = current_quantity
                    else:
                        # Default case
                        transaction.quantity_before = current_quantity
                        transaction.quantity_after = current_quantity
                    
                    updated_count += 1
                    
                except Exception as e:
                    print(f"Warning: Could not update transaction {transaction.id}: {e}")
                    continue
            
            commit()
            print(f"✓ Updated {updated_count} transaction records with quantity tracking")
            
    except Exception as e:
        print(f"✗ Error updating transaction data: {e}")
        return False
    
    return True

if __name__ == "__main__":
    print("=" * 60)
    print("INVENTORY V1 PONY ORM MIGRATION")
    print("=" * 60)
    
    # Step 1: Run main migration
    if not run_migration():
        print("Migration failed. Exiting.")
        sys.exit(1)
    
    # Step 2: Verify relationships
    if not verify_model_relationships():
        print("Relationship verification failed. Please check your models.")
        sys.exit(1)
    
    # Step 3: Update transaction data
    if not update_existing_transaction_data():
        print("Transaction data update failed. Manual intervention may be required.")
        sys.exit(1)
    
    print("\n" + "=" * 60)
    print("MIGRATION COMPLETED SUCCESSFULLY!")
    print("=" * 60)
    print("\nYour FastAPI application should now work with the new models.")
    print("Please test all inventory-related endpoints to ensure everything works correctly.")
