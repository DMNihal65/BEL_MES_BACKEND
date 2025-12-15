# BEL MES BACKEND - Database Schema Documentation

## Overview

The BEL MES backend uses **PostgreSQL** as the primary database with **Pony ORM** for object-relational mapping. The database is organized into multiple schemas for better organization and separation of concerns.

---

## Database Schemas

### 1. `auth` Schema - Authentication & Authorization

#### Tables:
- **users** - User accounts
  - `id`, `email`, `username`, `hashed_password`, `role`, `created_at`, `is_active`
  - Foreign Key: `role` → `user_roles`

- **user_roles** - User role definitions
  - `id`, `role_name`, `description`, `created_at`
  - One-to-many: `users`

- **machine_credentials** - Machine authentication
  - Credentials for machine access

**Relationships:**
- User → UserRole (Many-to-One)
- User ← ProductionLog, Document, etc. (One-to-Many)

---

### 2. `master_order` Schema - Orders & Projects

#### Tables:
- **projects** - Production projects
  - `id`, `project_name`, `project_code`, `start_date`, `end_date`, `status`
  - One-to-many: `orders`

- **orders** - Production orders
  - `id`, `production_order`, `part_number`, `part_description`, `required_quantity`, 
    `launched_quantity`, `project_id`, `order_date`, `due_date`, `status`
  - Foreign Key: `project_id` → `projects`
  - One-to-many: `operations`, `pdc_records`

- **operations** - Manufacturing operations
  - `id`, `operation_number`, `operation_description`, `machine_id`, `order_id`, 
    `setup_time`, `cycle_time`, `operation_sequence`
  - Foreign Key: `order_id` → `orders`, `machine_id` → `machines`
  - One-to-many: `programs`

- **programs** - CNC programs
  - `id`, `program_name`, `program_number`, `operation_id`, `file_path`, `created_at`
  - Foreign Key: `operation_id` → `operations`

- **machines** - Machine definitions
  - `id`, `machine_name`, `machine_make`, `machine_type`, `model_number`, 
    `status`, `capacity`
  - One-to-many: `operations`, `machine_raw`, etc.

**Relationships:**
```
Project → Orders → Operations → Programs
         ↓
      PDC Records
```

---

### 3. `production` Schema - Production Data

#### Tables:
- **status_lookup** - Machine status definitions
  - `status_id`, `status_name`
  - One-to-many: `machine_raw`, `machine_raw_live`

- **machine_raw** - Historical machine data
  - `id`, `machine_id`, `timestamp`, `status`, `op_mode`, `prog_status`, 
    `selected_program`, `active_program`, `program_number`, `part_count`, 
    `job_in_progress`, `part_status`
  - Foreign Keys: `status` → `status_lookup`, 
                 `machine_id` → `machines`,
                 `scheduled_job` → `operations`,
                 `actual_job` → `operations`

- **machine_raw_live** - Live machine data (single row per machine)
  - `machine_id` (PK), `timestamp`, `status`, `op_mode`, `prog_status`, 
    `selected_program`, `active_program`, `part_count`, `job_status`, 
    `job_in_progress`, `program_number`
  - Same relationships as `machine_raw`
  - **Special**: Has `get_order_details()` method to fetch order info

- **shift_summary** - Shift-wise production summary
  - `id`, `machine_id`, `shift`, `timestamp`, `updatedate`, `off_time`, 
    `idle_time`, `production_time`, `total_parts`, `good_parts`, `bad_parts`, 
    `availability`, `performance`, `quality`, `oee`, availability_loss, 
    performance_loss, quality_loss

- **shift_info** - Shift timing configuration
  - `id`, `start_time`, `end_time`

- **config_info** - Machine configuration
  - `id`, `machine_id` (unique), `shift_duration`, `planned_non_production_time`, 
    `planned_downtime`, `updatedate`

- **machine_downtimes** - Downtime tracking
  - `id`, `machine_id`, `priority`, `category`, `description`, `open_dt`, 
    `inprogress_dt`, `closed_dt`, `reported_by`, `action_taken`

- **oee_issue** - OEE issue tracking
  - `category`, `description`, `machine`, `timestamp`, `reported_by`, `end_timestamp`

**Relationships:**
```
Machines → MachineRaw (historical)
Machines → MachineRawLive (current)
Machines → ShiftSummary
StatusLookup → MachineRaw
Operations → MachineRaw (scheduled_job, actual_job)
```

---

### 4. `scheduling` Schema - Scheduling Data

#### Tables:
- **planned_schedule_items** - Planned schedule items
  - `id`, `operation_id`, `machine_id`, `start_time`, `end_time`, `order_id`, 
    `priority`, `status`, `created_at`, `updated_at`
  - Foreign Keys: `operation_id` → `operations`,
                 `machine_id` → `machines`,
                 `order_id` → `orders`

- **reschedule_history** - Reschedule tracking
  - `id`, `schedule_item_id`, `old_start_time`, `new_start_time`, 
    `rescheduled_by_operator`, `reason`, `created_at`
  - Foreign Keys: `schedule_item_id` → `planned_schedule_items`,
                 `rescheduled_by_operator` → `users`

- **production_logs** - Production logging
  - `id`, `operation_id`, `machine_id`, `operator`, `start_time`, `end_time`, 
    `part_count`, `good_parts`, `bad_parts`, `notes`, `created_at`
  - Foreign Keys: `operation_id` → `operations`,
                 `machine_id` → `machines`,
                 `operator` → `users`

**Relationships:**
```
Operations ← PlannedScheduleItems
Machines ← PlannedScheduleItems
Orders ← PlannedScheduleItems
PlannedScheduleItems ← RescheduleHistory
```

---

### 5. `quality` Schema - Quality Control

#### Tables:
- **master_boc** - Bill of Components
  - `id`, `part_number`, `component_part_number`, `component_description`, 
    `quantity_per_unit`, `unit_of_measure`, `document_id`, `created_at`
  - Foreign Key: `document_id` → `document_management_v2.documentv2`

- Additional quality inspection tables (refer to `app/models/quality.py`)

**Relationships:**
```
MasterBOC → DocumentV2 (BOC documents)
```

---

### 6. `inventoryv1` Schema - Inventory Management

#### Tables:
- **inventory_categories** - Material categories
  - `id`, `category_name`, `description`, `created_by`, `created_at`, `updated_at`
  - Foreign Key: `created_by` → `users`

- **inventory_subcategories** - Sub-categories
  - `id`, `subcategory_name`, `description`, `category_id`, `created_by`, 
    `created_at`, `updated_at`
  - Foreign Keys: `category_id` → `inventory_categories`,
                 `created_by` → `users`

- **inventory_items** - Inventory items
  - `id`, `item_code`, `item_name`, `description`, `category_id`, `subcategory_id`, 
    `unit_of_measure`, `stock_quantity`, `min_stock_level`, `max_stock_level`, 
    `unit_price`, `supplier`, `created_by`, `created_at`, `updated_at`
  - Foreign Keys: `category_id` → `inventory_categories`,
                 `subcategory_id` → `inventory_subcategories`,
                 `created_by` → `users`

- **calibration_schedules** - Instrument calibration
  - `id`, `instrument_name`, `instrument_id`, `calibration_due_date`, 
    `calibration_period_days`, `responsible_person`, `created_by`, `created_at`
  - Foreign Key: `created_by` → `users`

- **calibration_histories** - Calibration history
  - `id`, `schedule_id`, `calibration_date`, `next_due_date`, `calibration_status`, 
    `certificate_number`, `performed_by`, `remarks`, `created_at`
  - Foreign Key: `schedule_id` → `calibration_schedules`,
                 `performed_by` → `users`

- **inventory_requests** - Material requests
  - Request and approval workflow
  - Foreign Keys: `requested_by` → `users`,
                 `approved_by` → `users`

- **inventory_transactions** - Transaction logs
  - Transaction history

**Relationships:**
```
User → InventoryCategory
User → InventorySubCategory
User → InventoryItem
Category → SubCategory → InventoryItem
User → CalibrationSchedule → CalibrationHistory
```

---

### 7. `document_management` & `document_management_v2` Schemas

#### document_management Schema (v1):
- **doc_folders** - Folder hierarchy
- **doc_types** - Document type definitions
- **documents** - Document records
- **document_versions** - Version control
- **document_access_logs** - Access tracking

#### document_management_v2 Schema (v2):
- **folderv2** - Enhanced folder system
  - `id`, `folder_name`, `parent_folder_id`, `folder_path`, `created_by`, 
    `created_at`, `updated_at`, `is_active`
  - Foreign Keys: `parent_folder_id` → `folderv2` (self-referential),
                 `created_by` → `users`

- **documenttypev2** - Document type definitions
  - `id`, `type_name`, `description`, `file_extensions` (JSON), `is_active`

- **documentv2** - Documents
  - `id`, `folder_id`, `document_name`, `description`, `doc_type_id`, 
    `minio_object_id`, `file_size`, `checksum`, `metadata` (JSON), 
    `created_by`, `created_at`, `is_active`, `latest_version_id`
  - Foreign Keys: `folder_id` → `folderv2`,
                 `doc_type_id` → `documenttypev2`,
                 `created_by` → `users`,
                 `latest_version_id` → `documentversionv2`

- **documentversionv2** - Document versions
  - `id`, `document_id`, `version_number`, `minio_object_id`, `file_size`, 
    `checksum`, `metadata` (JSON), `created_by`, `created_at`, `status`
  - Foreign Keys: `document_id` → `documentv2`,
                 `created_by` → `users`

- **documentaccesslogv2** - Access logs
  - `id`, `document_id`, `version_id`, `user_id`, `action_type`, 
    `action_timestamp`, `ip_address`
  - Foreign Keys: `document_id` → `documentv2`,
                 `version_id` → `documentversionv2`,
                 `user_id` → `users`

**Relationships:**
```
User → FolderV2 (created_by)
FolderV2 → FolderV2 (parent)
FolderV2 → DocumentV2
DocumentTypeV2 → DocumentV2
DocumentV2 → DocumentVersionV2 (versions)
DocumentV2 → DocumentVersionV2 (latest_version_id)
DocumentV2 → DocumentAccessLogV2
```

---

### 8. `logs` Schema - Logging & Notifications

#### Tables:
- **machine_status_logs** - Machine status logging
  - `id`, `machine_id`, `machine_make`, `status_name`, `description`, 
    `updated_at`, `created_by`, `is_acknowledged`, `acknowledged_by`, 
    `acknowledged_at`, `read`

- **raw_material_status_logs** - Raw material status
  - Similar structure to machine_status_logs

- **pokayoke_checklists** - Quality checklists
  - `id`, `name`, `description`, `created_at`, `created_by`, `is_active`
  - One-to-many: `pokayoke_checklist_items`

- **pokayoke_checklist_items** - Checklist items
  - `id`, `checklist_id`, `item_text`, `sequence_number`, `item_type`, 
    `is_required`, `expected_value`, `created_at`
  - Foreign Key: `checklist_id` → `pokayoke_checklists`

- **pokayoke_machine_assignments** - Checklist assignments
  - `id`, `checklist_id`, `machine_id`, `machine_make`, `assigned_at`, 
    `assigned_by`, `is_active`
  - Foreign Key: `checklist_id` → `pokayoke_checklists`

- **pokayoke_completed_logs** - Completed checklists
  - `id`, `checklist_id`, `machine_id`, `operator_id`, `production_order`, 
    `part_number`, `completed_at`, `all_items_passed`, `comments`, `read`
  - Foreign Key: `checklist_id` → `pokayoke_checklists`
  - One-to-many: `pokayoke_item_responses`

- **pokayoke_item_responses** - Item responses
  - `id`, `completed_log_id`, `item_id`, `item_text`, `response_value`, 
    `is_conforming`, `timestamp`
  - Foreign Key: `completed_log_id` → `pokayoke_completed_logs`

- **machine_calibration_logs** - Calibration reminders
  - `id`, `timestamp`, `calibration_due_date`, `machine_id`, `read`
  - Foreign Key: `machine_id` → `machines`
  - **Special**: Has `after_insert()` hook for notifications

- **instrument_calibration_logs** - Instrument calibration
  - `id`, `timestamp`, `calibration_due_date`, `instrument_id`, `read`
  - Foreign Key: `instrument_id` → `calibration_schedules`
  - **Special**: Has `after_insert()` hook for notifications

- **asset_logs** - Asset logging
  - `id`, `machine_name`, `from_time`, `to_time`, `status`, `remarks`, `created_at`

**Relationships:**
```
PokaYokeChecklist → ChecklistItems
PokaYokeChecklist → MachineAssignments
PokaYokeCompletedLog → ItemResponses
MachineCalibrationLog → Machine (triggers notifications)
InstrumentCalibrationLog → CalibrationSchedule (triggers notifications)
```

---

### 9. `EMS` Schema - Energy Monitoring

#### Tables:
- Energy consumption tracking
- OPC-UA integration data
- Refer to `app/models/energymonitoring.py`

---

### 10. `hr_schema` & `finance_schema`

#### HR Schema:
- **employees** - Employee records
  - `id`, `name`, `email`, `department`
  - One-to-many: `salary_records`

#### Finance Schema:
- **salary_records** - Salary information
  - Foreign Key: `employee_id` → `hr_schema.employees`

**Relationships:**
```
Employee → SalaryRecord
```

---

## Cross-Schema Relationships

### Key Relationships:

1. **User-centric**:
   ```
   User → (created_by, approved_by, etc.) in:
   - Orders
   - Documents
   - Inventory
   - Checklists
   - Logs
   ```

2. **Order-centric**:
   ```
   Order → Operations → Programs → Machines
   Order → PlannedScheduleItems
   Order → ProductionLogs
   ```

3. **Machine-centric**:
   ```
   Machine → Operations
   Machine → MachineRaw
   Machine → MachineRawLive
   Machine → ShiftSummary
   Machine → PokaYokeAssignments
   ```

4. **Document-centric**:
   ```
   User → FolderV2 → DocumentV2 → DocumentVersionV2
   DocumentV2 → MasterBOC (Bill of Components)
   ```

---

## Database Connection

### Configuration (`app/database/connection.py`):
- Uses Pony ORM
- Database binding on startup
- Automatic schema creation
- Mapping generation

### Connection Settings:
```python
DB_HOST = "172.16.0.203"
DB_PORT = 5432
DB_NAME = "BEL_MES4"
DB_USER = "cmtismc"
DB_PASSWORD = "cmtismc@2025"
```

---

## Notes

- All timestamps use UTC
- Foreign keys maintain referential integrity
- Some tables have self-referential relationships (FolderV2)
- Special hooks for notifications (CalibrationLog entities)
- JSON fields for flexible data storage (metadata, file_extensions)

