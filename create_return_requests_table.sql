-- SQL CREATE TABLE statement for InventoryReturnRequest model
-- Based on the Pony ORM model definition - EXACT column names as in the model

CREATE TABLE inventoryv1.return_requests (
    id SERIAL PRIMARY KEY,
    inventory_item INTEGER NOT NULL,
    original_request INTEGER NOT NULL,
    requested_by INTEGER NOT NULL,
    quantity_to_return INTEGER NOT NULL,
    return_reason TEXT NOT NULL,
    status VARCHAR(50) NOT NULL,
    approved_by INTEGER,
    approved_at TIMESTAMP,
    actual_return_date TIMESTAMP,
    remarks TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    -- Foreign Key Constraints
    CONSTRAINT fk_return_requests_inventory_item 
        FOREIGN KEY (inventory_item) REFERENCES inventoryv1.items(id),
    
    CONSTRAINT fk_return_requests_original_request 
        FOREIGN KEY (original_request) REFERENCES inventoryv1.requests(id),
    
    -- Note: Uncomment and modify these based on your actual User table structure
    CONSTRAINT fk_return_requests_requested_by 
        FOREIGN KEY (requested_by) REFERENCES auth.users(id),
    
    CONSTRAINT fk_return_requests_approved_by 
        FOREIGN KEY (approved_by) REFERENCES auth.users(id)
);

-- Create indexes for better performance
CREATE INDEX idx_return_requests_inventory_item ON inventoryv1.return_requests(inventory_item);
CREATE INDEX idx_return_requests_original_request ON inventoryv1.return_requests(original_request);
CREATE INDEX idx_return_requests_requested_by ON inventoryv1.return_requests(requested_by);
CREATE INDEX idx_return_requests_status ON inventoryv1.return_requests(status);
CREATE INDEX idx_return_requests_created_at ON inventoryv1.return_requests(created_at);

-- Add a comment to the table
COMMENT ON TABLE inventoryv1.return_requests IS 'Request for returning inventory items';

-- Add column comments
COMMENT ON COLUMN inventoryv1.return_requests.id IS 'Primary key auto-increment';
COMMENT ON COLUMN inventoryv1.return_requests.inventory_item_id IS 'Foreign key to inventoryv1.items';
COMMENT ON COLUMN inventoryv1.return_requests.original_request_id IS 'Foreign key to original issue request';
COMMENT ON COLUMN inventoryv1.return_requests.requested_by_id IS 'User who wants to return the item';
COMMENT ON COLUMN inventoryv1.return_requests.quantity_to_return IS 'How much quantity to return';
COMMENT ON COLUMN inventoryv1.return_requests.return_reason IS 'Reason for returning the item';
COMMENT ON COLUMN inventoryv1.return_requests.status IS 'Status: Pending, Approved, Rejected, Completed';
COMMENT ON COLUMN inventoryv1.return_requests.approved_by_id IS 'User who approved the return request';
COMMENT ON COLUMN inventoryv1.return_requests.approved_at IS 'When the return was approved';
COMMENT ON COLUMN inventoryv1.return_requests.actual_return_date IS 'When the item was actually returned';
COMMENT ON COLUMN inventoryv1.return_requests.remarks IS 'Additional notes or remarks';
COMMENT ON COLUMN inventoryv1.return_requests.created_at IS 'When the record was created';
COMMENT ON COLUMN inventoryv1.return_requests.updated_at IS 'When the record was last updated';
