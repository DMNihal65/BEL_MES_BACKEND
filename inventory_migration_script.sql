-- Migration script for InventoryV1 models
-- This script safely migrates from old schema to new schema without data loss
-- Execute this script in order, do not skip steps

-- ==================================================
-- STEP 1: ADD NEW COLUMNS TO EXISTING TABLES
-- ==================================================

-- Add updated_at column to categories table
ALTER TABLE inventoryv1.categories 
ADD COLUMN updated_at TIMESTAMP;

-- Add updated_at column to subcategories table  
ALTER TABLE inventoryv1.subcategories 
ADD COLUMN updated_at TIMESTAMP;

-- Update items table - change updated_at to have default value
-- Note: updated_at already exists but we need to ensure it has proper default
ALTER TABLE inventoryv1.items 
ALTER COLUMN updated_at SET DEFAULT CURRENT_TIMESTAMP;

-- Update calibration_schedules table - change updated_at to have default value
ALTER TABLE inventoryv1.calibration_schedules 
ALTER COLUMN updated_at SET DEFAULT CURRENT_TIMESTAMP;

-- Add updated_at column to calibration_history table
ALTER TABLE inventoryv1.calibration_history 
ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;

-- Update requests table - change updated_at to have default value
ALTER TABLE inventoryv1.requests 
ALTER COLUMN updated_at SET DEFAULT CURRENT_TIMESTAMP;

-- Add new columns to transactions table
ALTER TABLE inventoryv1.transactions 
ADD COLUMN quantity_before INTEGER;

ALTER TABLE inventoryv1.transactions 
ADD COLUMN quantity_after INTEGER;

ALTER TABLE inventoryv1.transactions 
ADD COLUMN reference_return_request INTEGER;

ALTER TABLE inventoryv1.transactions 
ADD COLUMN transaction_reference VARCHAR(255);

ALTER TABLE inventoryv1.transactions 
ADD COLUMN location_from VARCHAR(255);

ALTER TABLE inventoryv1.transactions 
ADD COLUMN location_to VARCHAR(255);

-- ==================================================
-- STEP 2: CREATE NEW RETURN_REQUESTS TABLE
-- ==================================================

CREATE TABLE inventoryv1.return_requests (
    id SERIAL PRIMARY KEY,
    inventory_item INTEGER NOT NULL,
    original_request INTEGER NOT NULL,
    requested_by INTEGER NOT NULL,
    quantity_to_return INTEGER NOT NULL,
    return_reason VARCHAR(500) NOT NULL,
    status VARCHAR(50) NOT NULL,
    approved_by INTEGER,
    approved_at TIMESTAMP,
    actual_return_date TIMESTAMP,
    remarks TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- ==================================================
-- STEP 3: ADD FOREIGN KEY CONSTRAINTS
-- ==================================================

-- Foreign keys for return_requests table
ALTER TABLE inventoryv1.return_requests 
ADD CONSTRAINT fk_return_requests_inventory_item 
FOREIGN KEY (inventory_item) REFERENCES inventoryv1.items(id);

ALTER TABLE inventoryv1.return_requests 
ADD CONSTRAINT fk_return_requests_original_request 
FOREIGN KEY (original_request) REFERENCES inventoryv1.requests(id);

-- User table foreign keys  
ALTER TABLE inventoryv1.return_requests 
ADD CONSTRAINT fk_return_requests_requested_by 
FOREIGN KEY (requested_by) REFERENCES auth.users(id);

ALTER TABLE inventoryv1.return_requests 
ADD CONSTRAINT fk_return_requests_approved_by 
FOREIGN KEY (approved_by) REFERENCES auth.users(id);



-- Foreign key for transactions table reference to return requests
ALTER TABLE inventoryv1.transactions 
ADD CONSTRAINT fk_transactions_return_request 
FOREIGN KEY (reference_return_request) REFERENCES inventoryv1.return_requests(id);

-- ==================================================
-- STEP 4: UPDATE EXISTING DATA WITH SAFE DEFAULTS
-- ==================================================

-- Set current timestamp for existing records with NULL updated_at
UPDATE inventoryv1.categories 
SET updated_at = created_at 
WHERE updated_at IS NULL;

UPDATE inventoryv1.subcategories 
SET updated_at = created_at 
WHERE updated_at IS NULL;

UPDATE inventoryv1.items 
SET updated_at = COALESCE(updated_at, created_at);

UPDATE inventoryv1.calibration_schedules 
SET updated_at = COALESCE(updated_at, created_at);

UPDATE inventoryv1.calibration_history 
SET updated_at = created_at 
WHERE updated_at IS NULL;

UPDATE inventoryv1.requests 
SET updated_at = COALESCE(updated_at, created_at);

-- For transactions table, we need to populate quantity_before and quantity_after
-- This is more complex and depends on your business logic
-- Here's a basic approach - you may need to customize this based on your data

-- Option 1: Set both to current quantity (safe default)
UPDATE inventoryv1.transactions t
SET 
    quantity_before = COALESCE(
        (SELECT quantity FROM inventoryv1.items WHERE id = t.inventory_item_id), 
        0
    ),
    quantity_after = COALESCE(
        (SELECT quantity FROM inventoryv1.items WHERE id = t.inventory_item_id), 
        0
    )
WHERE quantity_before IS NULL OR quantity_after IS NULL;

-- ==================================================
-- STEP 5: ADD NOT NULL CONSTRAINTS FOR REQUIRED FIELDS
-- ==================================================

-- Make quantity_before and quantity_after NOT NULL after populating data
ALTER TABLE inventoryv1.transactions 
ALTER COLUMN quantity_before SET NOT NULL;

ALTER TABLE inventoryv1.transactions 
ALTER COLUMN quantity_after SET NOT NULL;

-- ==================================================
-- STEP 6: CREATE INDEXES FOR PERFORMANCE
-- ==================================================

-- Indexes for return_requests table
CREATE INDEX idx_return_requests_inventory_item ON inventoryv1.return_requests(inventory_item);
CREATE INDEX idx_return_requests_original_request ON inventoryv1.return_requests(original_request);
CREATE INDEX idx_return_requests_requested_by ON inventoryv1.return_requests(requested_by);
CREATE INDEX idx_return_requests_status ON inventoryv1.return_requests(status);
CREATE INDEX idx_return_requests_created_at ON inventoryv1.return_requests(created_at);

-- Additional indexes for transactions table
CREATE INDEX idx_transactions_return_request ON inventoryv1.transactions(reference_return_request);
CREATE INDEX idx_transactions_reference ON inventoryv1.transactions(transaction_reference);

-- ==================================================
-- STEP 7: VERIFICATION QUERIES
-- ==================================================

-- Run these queries to verify the migration was successful:

-- Check if all tables exist
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'inventoryv1' 
ORDER BY table_name;

-- Check column structure of return_requests table
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns 
WHERE table_schema = 'inventoryv1' AND table_name = 'return_requests'
ORDER BY ordinal_position;

-- Check column structure of transactions table
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns 
WHERE table_schema = 'inventoryv1' AND table_name = 'transactions'
ORDER BY ordinal_position;

-- Verify foreign key constraints
SELECT 
    tc.constraint_name,
    tc.table_name,
    kcu.column_name,
    ccu.table_name AS foreign_table_name,
    ccu.column_name AS foreign_column_name
FROM information_schema.table_constraints AS tc
JOIN information_schema.key_column_usage AS kcu
    ON tc.constraint_name = kcu.constraint_name
    AND tc.table_schema = kcu.table_schema
JOIN information_schema.constraint_column_usage AS ccu
    ON ccu.constraint_name = tc.constraint_name
    AND ccu.table_schema = tc.table_schema
WHERE tc.constraint_type = 'FOREIGN KEY' 
    AND tc.table_schema = 'inventoryv1'
ORDER BY tc.table_name, tc.constraint_name;

-- ==================================================
-- ROLLBACK SCRIPT (USE ONLY IF NEEDED)
-- ==================================================

/*
-- ONLY USE THIS SECTION IF YOU NEED TO ROLLBACK THE MIGRATION
-- WARNING: This will remove the new table and columns - use with caution

-- Remove foreign key constraints first
ALTER TABLE inventoryv1.transactions DROP CONSTRAINT IF EXISTS fk_transactions_return_request;
ALTER TABLE inventoryv1.return_requests DROP CONSTRAINT IF EXISTS fk_return_requests_inventory_item;
ALTER TABLE inventoryv1.return_requests DROP CONSTRAINT IF EXISTS fk_return_requests_original_request;

-- Drop new table
DROP TABLE IF EXISTS inventoryv1.return_requests;

-- Remove new columns from transactions table
ALTER TABLE inventoryv1.transactions DROP COLUMN IF EXISTS quantity_before;
ALTER TABLE inventoryv1.transactions DROP COLUMN IF EXISTS quantity_after;
ALTER TABLE inventoryv1.transactions DROP COLUMN IF EXISTS reference_return_request;
ALTER TABLE inventoryv1.transactions DROP COLUMN IF EXISTS transaction_reference;
ALTER TABLE inventoryv1.transactions DROP COLUMN IF EXISTS location_from;
ALTER TABLE inventoryv1.transactions DROP COLUMN IF EXISTS location_to;

-- Remove updated_at columns from tables that didn't have them
ALTER TABLE inventoryv1.categories DROP COLUMN IF EXISTS updated_at;
ALTER TABLE inventoryv1.subcategories DROP COLUMN IF EXISTS updated_at;
ALTER TABLE inventoryv1.calibration_history DROP COLUMN IF EXISTS updated_at;
*/
