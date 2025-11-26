-- Migration script to reorder project priorities
-- This script:
-- 1. Sets priority to NULL for all projects that have ONLY inactive orders (or no PartScheduleStatus)
-- 2. Reorders priorities as 1, 2, 3, ... for all projects that have at least one active order

BEGIN;

-- Step 1: Set priority to NULL for projects that have ONLY inactive orders (or no active orders)
-- A project gets NULL priority if it has no active orders
UPDATE master_order.projects p
SET priority = NULL
WHERE NOT EXISTS (
    SELECT 1
    FROM master_order.orders o
    INNER JOIN scheduling.part_schedule_status pss ON o.production_order = pss.production_order
    WHERE o.project = p.id
    AND pss.status = 'active'
);

-- Step 2: Reorder priorities for projects that have at least one active order
-- Assign sequential priorities 1, 2, 3, ... ordered by current priority (if exists) or project ID
WITH active_projects AS (
    SELECT DISTINCT p.id AS project_id
    FROM master_order.projects p
    INNER JOIN master_order.orders o ON o.project = p.id
    INNER JOIN scheduling.part_schedule_status pss ON o.production_order = pss.production_order
    WHERE pss.status = 'active'
),
ranked_projects AS (
    SELECT 
        ap.project_id,
        ROW_NUMBER() OVER (
            ORDER BY 
                COALESCE(p.priority, 999999) ASC,  -- Projects with NULL priority go last
                p.id ASC  -- Use project ID as tiebreaker
        ) AS new_priority
    FROM active_projects ap
    INNER JOIN master_order.projects p ON p.id = ap.project_id
)
-- Update projects with new sequential priorities
UPDATE master_order.projects p
SET priority = rp.new_priority
FROM ranked_projects rp
WHERE p.id = rp.project_id;

-- Optional: Verify the results
-- Check count of projects with NULL priority (should be inactive orders)
-- SELECT COUNT(*) FROM master_order.projects WHERE priority IS NULL;

-- Check count of projects with sequential priorities (should be active orders)
-- SELECT COUNT(*), MIN(priority), MAX(priority) 
-- FROM master_order.projects 
-- WHERE priority IS NOT NULL;

COMMIT;

-- Rollback instructions:
-- If you need to rollback, you would need to restore from a backup
-- or manually reassign priorities based on your previous priority values

