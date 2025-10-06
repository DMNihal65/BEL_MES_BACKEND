-- Migration script to add pm_due_date column to machines table
-- Run this script to add the new pm_due_date column to the existing machines table

-- Add the pm_due_date column to the machines table in master_order schema
ALTER TABLE master_order.machines 
ADD COLUMN pm_due_date TIMESTAMP NULL;

-- Add a comment to document the column purpose
COMMENT ON COLUMN master_order.machines.pm_due_date IS 'PM (Preventive Maintenance) due date for the machine';

-- Optional: Create an index on pm_due_date for better query performance
-- CREATE INDEX idx_machines_pm_due_date ON master_order.machines(pm_due_date);
