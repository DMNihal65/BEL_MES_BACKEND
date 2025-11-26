# InventoryV1 Models Migration Guide

This guide will help you migrate from your old inventory models to the new enhanced models without losing any data.

## Overview of Changes

### New Features Added:
1. **InventoryReturnRequest** - New model for handling return requests
2. **Enhanced Transaction Tracking** - Added quantity_before/quantity_after fields
3. **Better Audit Trail** - Added location tracking and transaction references
4. **Consistent Timestamps** - Added updated_at fields where missing

### Models Modified:
- `InventoryCategory` - Added updated_at field
- `InventorySubCategory` - Added updated_at field  
- `InventoryItem` - Added return_requests relationship, fixed updated_at default
- `CalibrationSchedule` - Fixed updated_at default
- `CalibrationHistory` - Added updated_at field
- `InventoryRequest` - Added return_requests relationship, fixed updated_at default
- `InventoryTransaction` - Enhanced with quantity tracking and audit fields

## Migration Steps

### Step 1: Backup Your Database
```bash
# Create a backup before migration
pg_dump -h your_host -U your_user -d your_database > inventory_backup_$(date +%Y%m%d).sql
```

### Step 2: Stop Your FastAPI Application
```bash
# Stop your application to prevent database conflicts
sudo systemctl stop your-fastapi-service
# or
pkill -f "python.*main.py"
```

### Step 3: Execute SQL Migration
```bash
# Connect to your PostgreSQL database
psql -h your_host -U your_user -d your_database

# Execute the migration script
\i inventory_migration_script.sql
```

### Step 4: Update Your Application Models
Replace your old `app/models/inventoryv1.py` with the new version that includes all the enhanced models.

### Step 5: Run Pony ORM Migration
```bash
# Make the Python migration script executable
chmod +x inventory_pony_migration.py

# Run the Pony ORM migration
python inventory_pony_migration.py
```

### Step 6: Restart Your Application
```bash
# Start your FastAPI application
python app/main.py
# or
sudo systemctl start your-fastapi-service
```

### Step 7: Verify Migration Success
1. Check that your FastAPI application starts without errors
2. Test inventory-related API endpoints
3. Verify that existing data is accessible
4. Test new return request functionality

## Troubleshooting

### Common Issues and Solutions

#### Issue 1: Foreign Key Constraint Errors
```
Error: column "reference_return_request" does not exist
```
**Solution:** Make sure you executed the SQL migration script completely before updating your Pony ORM models.

#### Issue 2: Table Already Exists Errors
```
Error: relation "return_requests" already exists
```
**Solution:** The table was partially created. Either drop it and re-run the migration, or skip the CREATE TABLE step.

#### Issue 3: User Table Foreign Key Issues
```
Error: foreign key constraint fails
```
**Solution:** Update the foreign key constraints in the migration script to match your actual User table structure.

#### Issue 4: Pony ORM Mapping Errors
```
Error: Cannot generate mapping
```
**Solution:** Ensure your database connection is working and all tables exist before running the Pony migration.

## Verification Queries

After migration, run these queries to verify everything is working:

```sql
-- Check table structure
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'inventoryv1' ORDER BY table_name;

-- Verify data integrity
SELECT 
    'categories' as table_name, count(*) as record_count 
FROM inventoryv1.categories
UNION ALL
SELECT 'subcategories', count(*) FROM inventoryv1.subcategories
UNION ALL  
SELECT 'items', count(*) FROM inventoryv1.items
UNION ALL
SELECT 'requests', count(*) FROM inventoryv1.requests
UNION ALL
SELECT 'transactions', count(*) FROM inventoryv1.transactions
UNION ALL
SELECT 'return_requests', count(*) FROM inventoryv1.return_requests;

-- Check new columns
SELECT column_name, data_type, is_nullable 
FROM information_schema.columns 
WHERE table_schema = 'inventoryv1' AND table_name = 'transactions'
AND column_name IN ('quantity_before', 'quantity_after', 'reference_return_request_id');
```

## API Testing

Test these endpoints after migration:

1. **GET /inventory/categories** - Should return all categories
2. **GET /inventory/items** - Should return all items
3. **GET /inventory/requests** - Should return all requests
4. **POST /inventory/return-requests** - Should work with new return request functionality
5. **GET /inventory/transactions** - Should show enhanced transaction data

## Rollback Procedure

If you need to rollback the migration:

1. Stop your application
2. Restore from backup:
   ```bash
   psql -h your_host -U your_user -d your_database < inventory_backup_YYYYMMDD.sql
   ```
3. Revert to your old model files
4. Restart your application

## Support

If you encounter issues during migration:

1. Check the FastAPI logs for specific error messages
2. Verify database connectivity
3. Ensure all foreign key relationships are properly defined
4. Check that your User model relationships are correctly configured

## Post-Migration Tasks

After successful migration:

1. Update your API documentation
2. Train users on new return request functionality  
3. Update any client applications to use new endpoints
4. Monitor system performance with the new schema
5. Plan for future data archival if transaction volume increases

---

**Important:** Always test the migration on a development environment before applying to production!
