# BEL MES - Complete Database Schema Documentation

## Table of Contents
1. [Schema Overview](#schema-overview)
2. [Schema Details](#schema-details)
   - [auth](#1-auth-schema)
   - [master_order](#2-master_order-schema)
   - [scheduling](#3-scheduling-schema)
   - [production](#4-production-schema)
   - [inventory](#5-inventory-schema)
   - [inventoryv1](#6-inventoryv1-schema)
   - [quality](#7-quality-schema)
   - [document_management](#8-document_management-schema)
   - [document_management_v2](#9-document_management_v2-schema)
   - [logs](#10-logs-schema)
   - [ems](#11-ems-schema)
   - [hr_schema](#12-hr_schema)
   - [finance_schema](#13-finance_schema)

---

## Schema Overview

The BEL MES database uses **13 schemas** organized by functionality:

| Schema | Purpose | Tables |
|--------|---------|--------|
| `auth` | Authentication & Users | 3 |
| `master_order` | Orders, Operations, Machines | 15 |
| `scheduling` | Production Scheduling | 7 |
| `production` | Production Monitoring | 8 |
| `inventory` | Basic Inventory | 8 |
| `inventoryv1` | Enhanced Inventory | 6 |
| `quality` | Quality Control | 4 |
| `document_management` | Documents (v1) | 5 |
| `document_management_v2` | Documents (v2) | 4 |
| `logs` | Logging & Notifications | 10 |
| `ems` | Energy Monitoring | 4 |
| `hr_schema` | HR Management | 1 |
| `finance_schema` | Finance | 1 |

**Total Tables**: ~75 tables

---

## Schema Details

### 1. auth Schema

#### 1.1 user_roles
**Purpose**: Define user role types

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| role_name | VARCHAR | UNIQUE, NOT NULL | Role name (admin, operator, etc.) |
| access_list | VARCHAR | NOT NULL | Access permissions (JSON string) |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |

**Relationships**:
- One-to-Many: `users` (reverse: `users.role`)

#### 1.2 users
**Purpose**: User accounts

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| email | VARCHAR | UNIQUE, NOT NULL | User email |
| username | VARCHAR | UNIQUE, NOT NULL | Username |
| hashed_password | VARCHAR | NOT NULL | Bcrypt hashed password |
| role | INTEGER | FK → user_roles(id), NOT NULL | User role |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| is_active | BOOLEAN | NOT NULL, DEFAULT TRUE | Active status |

**Foreign Keys**:
- `role` → `auth.user_roles(id)`

**Relationships**:
- Many-to-One: `role` → `UserRole`
- One-to-Many: `user_logs`, `production_logs`, `reschedule_histories`, `documents`, `doc_folders`, `inventory_*`, `document_*`, etc.

#### 1.3 machine_credentials
**Purpose**: Machine authentication credentials

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| machine | INTEGER | FK → machines(id), UNIQUE | Machine reference |
| password | VARCHAR | NOT NULL | Machine password |

**Foreign Keys**:
- `machine` → `master_order.machines(id)` (one-to-one)

---

### 2. master_order Schema

#### 2.1 WorkCenter
**Purpose**: Work centers for operations

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| code | VARCHAR | NOT NULL | Work center code |
| plant_id | VARCHAR | NOT NULL | Plant identifier |
| work_center_name | VARCHAR | NULLABLE | Work center name |
| description | VARCHAR | NULLABLE | Description |
| is_schedulable | BOOLEAN | DEFAULT FALSE | Can be scheduled |

**Relationships**:
- One-to-Many: `machines`, `operations`

#### 2.2 machines
**Purpose**: Machine definitions

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| work_center | INTEGER | FK → WorkCenter(id), NOT NULL | Work center |
| type | VARCHAR | NOT NULL | Machine type |
| make | VARCHAR | NOT NULL | Machine make |
| model | VARCHAR | NOT NULL | Model number |
| year_of_installation | INTEGER | NULLABLE | Installation year |
| cnc_controller | VARCHAR | NULLABLE | CNC controller |
| cnc_controller_series | VARCHAR | NULLABLE | Controller series |
| remarks | VARCHAR | NULLABLE | Remarks |
| calibration_date | TIMESTAMP | NULLABLE | Last calibration |
| calibration_due_date | TIMESTAMP | NULLABLE | Calibration due |
| last_maintenance_date | TIMESTAMP | NULLABLE | Last maintenance |
| pm_due_date | TIMESTAMP | NULLABLE | PM due date |

**Foreign Keys**:
- `work_center` → `master_order.WorkCenter(id)`

**Relationships**:
- Many-to-One: `work_center` → `WorkCenter`, `credential` (one-to-one)
- One-to-Many: `shifts`, `downtimes`, `status`, `operations`, `planned_schedule_items`, `notification`, `planned_items`

#### 2.3 machine_shifts
**Purpose**: Machine shift schedules

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| machine | INTEGER | FK → machines(id), NOT NULL | Machine reference |
| shift_start | TIMESTAMP | NOT NULL | Shift start time |
| shift_end | TIMESTAMP | NOT NULL | Shift end time |
| is_active | BOOLEAN | NOT NULL, DEFAULT TRUE | Active status |

**Foreign Keys**:
- `machine` → `master_order.machines(id)`

#### 2.4 machine_downtimes
**Purpose**: Machine downtime tracking

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| machine | INTEGER | FK → machines(id), NOT NULL | Machine reference |
| start_time | TIMESTAMP | NOT NULL | Start time |
| end_time | TIMESTAMP | NOT NULL | End time |
| is_active | BOOLEAN | NOT NULL, DEFAULT TRUE | Active status |

**Foreign Keys**:
- `machine` → `master_order.machines(id)`

#### 2.5 status
**Purpose**: Status definitions

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| name | VARCHAR | NOT NULL | Status name |
| description | VARCHAR | NULLABLE | Description |

**Relationships**:
- One-to-Many: `machine_statuses`

#### 2.6 machine_status
**Purpose**: Machine status tracking

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| machine | INTEGER | FK → machines(id), NOT NULL | Machine reference |
| status | INTEGER | FK → status(id), NOT NULL | Status reference |
| description | VARCHAR | NULLABLE | Description |
| available_from | TIMESTAMP | NULLABLE | Status start |
| available_to | TIMESTAMP | NULLABLE | Status end |

**Foreign Keys**:
- `machine` → `master_order.machines(id)`
- `status` → `master_order.status(id)`

#### 2.7 projects
**Purpose**: Production projects

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| name | VARCHAR | NOT NULL | Project name |
| priority | INTEGER | NOT NULL | Priority level |
| start_date | TIMESTAMP | NOT NULL | Start date |
| end_date | TIMESTAMP | NOT NULL | End date |
| delivery_date | TIMESTAMP | NOT NULL | Delivery date |

**Relationships**:
- One-to-Many: `orders`

#### 2.8 orders
**Purpose**: Production orders

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| production_order | VARCHAR | UNIQUE, NOT NULL | Production order number |
| sale_order | VARCHAR | NULLABLE | Sales order |
| wbs_element | VARCHAR | NULLABLE | WBS element |
| part_number | VARCHAR | NOT NULL | Part number |
| part_description | VARCHAR | NULLABLE | Part description |
| total_operations | INTEGER | NOT NULL | Total operations |
| required_quantity | INTEGER | NOT NULL | Required quantity |
| launched_quantity | INTEGER | NOT NULL | Launched quantity |
| raw_material | INTEGER | FK → RawMaterial(id), NOT NULL | Raw material |
| plant_id | VARCHAR | NOT NULL | Plant identifier |
| project | INTEGER | FK → projects(id), NOT NULL | Project reference |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |

**Foreign Keys**:
- `raw_material` → `inventory.raw_materials(id)`
- `project` → `master_order.projects(id)`

**Relationships**:
- Many-to-One: `raw_material` → `RawMaterial`, `project` → `Project`
- One-to-Many: `operations`, `documents`, `tools`, `jigs_fixtures`, `mpps`, `planned_schedule_items`, `inventory_requests`, `documents_v2`, `order_tools`, `order_completed`, `planned_items`, `pdc_records`

#### 2.9 operations
**Purpose**: Manufacturing operations

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| order | INTEGER | FK → orders(id), NOT NULL | Order reference |
| operation_number | INTEGER | NOT NULL | Operation sequence |
| work_center | INTEGER | FK → WorkCenter(id), NOT NULL | Work center |
| machine | INTEGER | FK → machines(id), NOT NULL | Machine reference |
| operation_description | VARCHAR | NULLABLE | Description |
| setup_time | DECIMAL | NOT NULL | Setup time |
| ideal_cycle_time | DECIMAL | NOT NULL | Cycle time |

**Foreign Keys**:
- `order` → `master_order.orders(id)`
- `work_center` → `master_order.WorkCenter(id)`
- `machine` → `master_order.machines(id)`

**Relationships**:
- Many-to-One: `order` → `Order`, `work_center` → `WorkCenter`, `machine` → `Machine`
- One-to-Many: `process_plans`, `tools`, `jigs_fixtures`, `programs`, `mpps`, `planned_schedule_items`, `order_tools`, `production_logs`, `machine_raw_live_1`, `machine_raw_live_2`, `inventory_requests`, `planned_items`

#### 2.10 process_plan
**Purpose**: Process planning details

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| operation | INTEGER | FK → operations(id), NOT NULL | Operation reference |
| instructions | VARCHAR | NULLABLE | Instructions |
| images | VARCHAR | NULLABLE | Image references |
| remarks | VARCHAR | NULLABLE | Remarks |

**Foreign Keys**:
- `operation` → `master_order.operations(id)`

#### 2.11 programs
**Purpose**: CNC programs

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| operation | INTEGER | FK → operations(id), NOT NULL | Operation reference |
| program_name | VARCHAR | NOT NULL | Program name |
| program_number | VARCHAR | NOT NULL | Program number |
| version | VARCHAR | NOT NULL | Version |
| update_date | TIMESTAMP | NOT NULL | Update date |

**Foreign Keys**:
- `operation` → `master_order.operations(id)`

#### 2.12 order_tools
**Purpose**: Tools for orders

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| order | INTEGER | FK → orders(id), NOT NULL | Order reference |
| operation | INTEGER | FK → operations(id), NULLABLE | Operation reference |
| tool_id | INTEGER | FK → InventoryItem(id), NULLABLE | Tool reference |
| tool_name | VARCHAR | NOT NULL | Tool name |
| tool_number | VARCHAR | NOT NULL | Tool number |
| bel_partnumber | VARCHAR | NULLABLE | BEL part number |
| description | VARCHAR | NULLABLE | Description |
| quantity | INTEGER | NOT NULL, DEFAULT 1 | Quantity |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Update timestamp |

**Foreign Keys**:
- `order` → `master_order.orders(id)`
- `operation` → `master_order.operations(id)`
- `tool_id` → `inventoryv1.items(id)`

#### 2.13 tool_list
**Purpose**: Tool lists for operations

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| order | INTEGER | FK → orders(id), NOT NULL | Order reference |
| operation | INTEGER | FK → operations(id), NOT NULL | Operation reference |
| tool_id | VARCHAR | NOT NULL | Tool ID |

**Foreign Keys**:
- `order` → `master_order.orders(id)`
- `operation` → `master_order.operations(id)`

#### 2.14 jigs_and_fixtures_list
**Purpose**: Jigs and fixtures

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| order | INTEGER | FK → orders(id), NOT NULL | Order reference |
| operation | INTEGER | FK → operations(id), NOT NULL | Operation reference |
| jigs_id | VARCHAR | NOT NULL | Jigs ID |

**Foreign Keys**:
- `order` → `master_order.orders(id)`
- `operation` → `master_order.operations(id)`

#### 2.15 user_logs
**Purpose**: User login/logout tracking

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| user | INTEGER | FK → users(id), NOT NULL | User reference |
| login_timestamp | TIMESTAMP | NOT NULL | Login time |
| logout_timestamp | TIMESTAMP | NULLABLE | Logout time |

**Foreign Keys**:
- `user` → `auth.users(id)`

#### 2.16 mpp
**Purpose**: Master Production Planning

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| order | INTEGER | FK → orders(id), NOT NULL | Order reference |
| operation | INTEGER | FK → operations(id), NOT NULL | Operation reference |
| document | INTEGER | FK → documents(id), NULLABLE | Document reference |
| fixture_number | VARCHAR | NULLABLE | Fixture number |
| ipid_number | VARCHAR | NULLABLE | IPID number |
| datum_x | VARCHAR | NULLABLE | Datum X |
| datum_y | VARCHAR | NULLABLE | Datum Y |
| datum_z | VARCHAR | NULLABLE | Datum Z |
| work_instructions | JSON | NOT NULL, DEFAULT {"sections": []} | Work instructions |

**Foreign Keys**:
- `order` → `master_order.orders(id)`
- `operation` → `master_order.operations(id)`
- `document` → `document_management.documents(id)`

#### 2.17 order_completed
**Purpose**: Order completion tracking

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| order_id | INTEGER | FK → orders(id), NOT NULL | Order reference |
| is_completed | BOOLEAN | NOT NULL, DEFAULT FALSE | Completion status |
| triggered_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Trigger time |

**Foreign Keys**:
- `order_id` → `master_order.orders(id)`

---

### 3. scheduling Schema

#### 3.1 schedule_history
**Purpose**: Schedule generation versions

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| version | INTEGER | NOT NULL | Version number |
| is_active | BOOLEAN | NOT NULL, DEFAULT FALSE | Active status |
| generated_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Generation time |

**Relationships**:
- One-to-Many: `planned_schedule_items`

#### 3.2 part_schedule_status
**Purpose**: Part scheduling control

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| part_number | VARCHAR | NOT NULL | Part number |
| production_order | VARCHAR | UNIQUE, NOT NULL | Production order |
| status | VARCHAR | NOT NULL, DEFAULT 'inactive' | Status |
| start_date | TIMESTAMP | NULLABLE | Start date |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Update timestamp |

#### 3.3 planned_schedule_items
**Purpose**: Planned schedule results

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| order | INTEGER | FK → orders(id), NOT NULL | Order reference |
| operation | INTEGER | FK → operations(id), NOT NULL | Operation reference |
| machine | INTEGER | FK → machines(id), NOT NULL | Machine reference |
| initial_start_time | TIMESTAMP | NOT NULL | Initial start time |
| initial_end_time | TIMESTAMP | NOT NULL | Initial end time |
| total_quantity | INTEGER | NOT NULL | Total quantity |
| remaining_quantity | INTEGER | NOT NULL | Remaining quantity |
| status | VARCHAR | NULLABLE | Status |
| current_version | INTEGER | NULLABLE | Current version |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| schedule_history_id | INTEGER | FK → schedule_history(id), NULLABLE | Schedule history reference |

**Foreign Keys**:
- `order` → `master_order.orders(id)`
- `operation` → `master_order.operations(id)`
- `machine` → `master_order.machines(id)`
- `schedule_history_id` → `scheduling.schedule_history(id)`

**Relationships**:
- Many-to-One: `order` → `Order`, `operation` → `Operation`, `machine` → `Machine`, `schedule_history` → `ScheduleHistory`
- One-to-Many: `schedule_versions`

#### 3.4 schedule_versions
**Purpose**: Schedule version tracking

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| schedule_item | INTEGER | FK → planned_schedule_items(id), NOT NULL | Schedule item reference |
| version_number | INTEGER | NOT NULL | Version number |
| planned_start_time | TIMESTAMP | NOT NULL | Planned start time |
| planned_end_time | TIMESTAMP | NOT NULL | Planned end time |
| planned_quantity | INTEGER | NOT NULL | Planned quantity |
| completed_quantity | INTEGER | NOT NULL, DEFAULT 0 | Completed quantity |
| remaining_quantity | INTEGER | NOT NULL | Remaining quantity |
| is_active | BOOLEAN | NOT NULL, DEFAULT TRUE | Active status |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |

**Foreign Keys**:
- `schedule_item` → `scheduling.planned_schedule_items(id)`

**Relationships**:
- Many-to-One: `schedule_item` → `PlannedScheduleItem`
- One-to-Many: `reschedule_histories_as_current`, `reschedule_histories_as_previous`, `production_logs`

#### 3.5 reschedule_history
**Purpose**: Reschedule tracking

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| schedule_version | INTEGER | FK → schedule_versions(id), NOT NULL | Current version |
| previous_version | INTEGER | FK → schedule_versions(id), NOT NULL | Previous version |
| reason | VARCHAR | NOT NULL | Reason |
| rescheduled_by_operator | INTEGER | FK → users(id), NOT NULL | Operator reference |
| rescheduled_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Reschedule time |
| old_start_time | TIMESTAMP | NOT NULL | Old start time |
| old_end_time | TIMESTAMP | NOT NULL | Old end time |
| new_start_time | TIMESTAMP | NOT NULL | New start time |
| new_end_time | TIMESTAMP | NOT NULL | New end time |

**Foreign Keys**:
- `schedule_version` → `scheduling.schedule_versions(id)`
- `previous_version` → `scheduling.schedule_versions(id)`
- `rescheduled_by_operator` → `auth.users(id)`

#### 3.6 production_logs
**Purpose**: Production progress tracking

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| machine_id | INTEGER | NULLABLE | Machine ID |
| operation | INTEGER | FK → operations(id), NOT NULL | Operation reference |
| schedule_version | INTEGER | FK → schedule_versions(id), NULLABLE | Schedule version |
| operator | INTEGER | FK → users(id), NULLABLE | Operator reference |
| start_time | TIMESTAMP | NULLABLE | Start time |
| end_time | TIMESTAMP | NULLABLE | End time |
| quantity_completed | INTEGER | NULLABLE | Completed quantity |
| quantity_rejected | INTEGER | NULLABLE | Rejected quantity |
| notes | VARCHAR | NULLABLE | Notes |

**Foreign Keys**:
- `operation` → `master_order.operations(id)`
- `schedule_version` → `scheduling.schedule_versions(id)`
- `operator` → `auth.users(id)`

#### 3.7 planned_items
**Purpose**: Planned items table

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| order | INTEGER | FK → orders(id), NOT NULL | Order reference |
| operation | INTEGER | FK → operations(id), NOT NULL | Operation reference |
| machine | INTEGER | FK → machines(id), NOT NULL | Machine reference |
| initial_start_time | TIMESTAMP | NOT NULL | Initial start time |
| initial_end_time | TIMESTAMP | NOT NULL | Initial end time |
| total_quantity | INTEGER | NOT NULL | Total quantity |
| remaining_quantity | INTEGER | NOT NULL | Remaining quantity |
| status | VARCHAR | NULLABLE | Status |
| current_version | INTEGER | NULLABLE | Current version |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |

**Foreign Keys**:
- `order` → `master_order.orders(id)`
- `operation` → `master_order.operations(id)`
- `machine` → `master_order.machines(id)`

#### 3.8 pdc
**Purpose**: Production Data Collection

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| order_id | INTEGER | FK → orders(id), NOT NULL | Order reference |
| part_number | VARCHAR | NOT NULL | Part number |
| production_order | VARCHAR | NOT NULL | Production order |

**Foreign Keys**:
- `order_id` → `master_order.orders(id)`

---

### 4. production Schema

#### 4.1 status_lookup
**Purpose**: Machine status definitions

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| status_id | INTEGER | PK | Primary key |
| status_name | VARCHAR | UNIQUE, NOT NULL | Status name |

**Relationships**:
- One-to-Many: `machine_raw`, `machine_raw_live`

#### 4.2 machine_raw
**Purpose**: Historical machine data

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| machine_id | INTEGER | NOT NULL | Machine ID |
| timestamp | TIMESTAMP | NOT NULL, DEFAULT NOW | Timestamp |
| status | INTEGER | FK → status_lookup(status_id), NOT NULL | Status reference |
| op_mode | INTEGER | NULLABLE | Operating mode |
| prog_status | INTEGER | NULLABLE | Program status |
| selected_program | VARCHAR | NULLABLE | Selected program |
| active_program | VARCHAR | NULLABLE | Active program |
| program_number | VARCHAR | NULLABLE | Program number |
| part_count | INTEGER | NULLABLE | Part count |
| job_in_progress | INTEGER | NULLABLE | Job in progress |
| part_status | INTEGER | NULLABLE | Part status |
| scheduled_job | INTEGER | FK → operations(id), NULLABLE | Scheduled job |
| actual_job | INTEGER | FK → operations(id), NULLABLE | Actual job |

**Foreign Keys**:
- `status` → `production.status_lookup(status_id)`
- `scheduled_job` → `master_order.operations(id)`
- `actual_job` → `master_order.operations(id)`

#### 4.3 machine_raw_live
**Purpose**: Live machine data (one row per machine)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| machine_id | INTEGER | PK | Primary key (Machine ID) |
| timestamp | TIMESTAMP | NOT NULL, DEFAULT NOW | Timestamp |
| status | INTEGER | FK → status_lookup(status_id), NOT NULL | Status reference |
| op_mode | INTEGER | NULLABLE | Operating mode |
| prog_status | INTEGER | NULLABLE | Program status |
| selected_program | VARCHAR | NULLABLE | Selected program |
| active_program | VARCHAR | NULLABLE | Active program |
| part_count | INTEGER | NULLABLE | Part count |
| job_status | INTEGER | NULLABLE | Job status |
| job_in_progress | INTEGER | NULLABLE | Job in progress |
| program_number | INTEGER | NULLABLE | Program number |
| scheduled_job | INTEGER | FK → operations(id), NULLABLE | Scheduled job |
| actual_job | INTEGER | FK → operations(id), NULLABLE | Actual job |

**Foreign Keys**:
- `status` → `production.status_lookup(status_id)`
- `scheduled_job` → `master_order.operations(id)`
- `actual_job` → `master_order.operations(id)`

**Special Methods**:
- `get_order_details()` - Returns associated order details
- `get_summary_info()` - Returns machine summary info

#### 4.4 shift_summary
**Purpose**: Shift-wise production summary

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| machine_id | INTEGER | NOT NULL | Machine ID |
| shift | INTEGER | NOT NULL | Shift number |
| timestamp | TIMESTAMP | NOT NULL | Timestamp |
| updatedate | TIMESTAMP | NOT NULL, DEFAULT NOW, AUTO | Update timestamp |
| off_time | TIME | NULLABLE | Off time |
| idle_time | TIME | NULLABLE | Idle time |
| production_time | TIME | NULLABLE | Production time |
| total_parts | INTEGER | NULLABLE | Total parts |
| good_parts | INTEGER | NULLABLE | Good parts |
| bad_parts | INTEGER | NULLABLE | Bad parts |
| availability | DECIMAL(5,2) | NULLABLE | Availability % |
| performance | DECIMAL(5,2) | NULLABLE | Performance % |
| quality | DECIMAL(5,2) | NULLABLE | Quality % |
| availability_loss | DECIMAL(5,2) | NULLABLE | Availability loss |
| performance_loss | DECIMAL(5,2) | NULLABLE | Performance loss |
| quality_loss | DECIMAL(5,2) | NULLABLE | Quality loss |
| oee | DECIMAL(5,2) | NULLABLE | OEE % |

#### 4.5 shift_info
**Purpose**: Shift timing configuration

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| start_time | TIME | NOT NULL | Shift start time |
| end_time | TIME | NOT NULL | Shift end time |

#### 4.6 config_info
**Purpose**: Machine configuration

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| machine_id | INTEGER | UNIQUE, NOT NULL | Machine ID |
| shift_duration | INTEGER | NOT NULL | Shift duration (minutes) |
| planned_non_production_time | INTEGER | NOT NULL | Planned non-production time |
| planned_downtime | INTEGER | NOT NULL | Planned downtime |
| updatedate | TIMESTAMP | NOT NULL, DEFAULT NOW | Update timestamp |

#### 4.7 machine_downtimes
**Purpose**: Machine downtime tracking

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| machine_id | INTEGER | NOT NULL | Machine ID |
| priority | INTEGER | NULLABLE | Priority |
| category | VARCHAR | NULLABLE | Category |
| description | VARCHAR | NULLABLE | Description |
| open_dt | TIMESTAMP | NOT NULL | Open date/time |
| inprogress_dt | TIMESTAMP | NULLABLE | In progress date/time |
| closed_dt | TIMESTAMP | NULLABLE | Closed date/time |
| reported_by | INTEGER | NULLABLE | Reported by user |
| action_taken | VARCHAR | NULLABLE | Action taken |

#### 4.8 oee_issue
**Purpose**: OEE issue tracking

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| category | VARCHAR | NOT NULL | Category |
| description | VARCHAR | NOT NULL | Description |
| machine | INTEGER | NOT NULL | Machine ID |
| timestamp | TIMESTAMP | NOT NULL | Timestamp |
| reported_by | INTEGER | NOT NULL | Reported by user |
| end_timestamp | TIMESTAMP | NOT NULL | End timestamp |

**Note**: All columns together serve as composite primary key

---

### 5. inventory Schema

#### 5.1 inventory_status
**Purpose**: Inventory status definitions

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| name | VARCHAR | NOT NULL | Status name |
| description | VARCHAR | NULLABLE | Description |

**Relationships**:
- One-to-Many: `raw_materials`, `instruments`, `tools`, `jigs_fixtures`

#### 5.2 tool_types
**Purpose**: Tool type definitions

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| name | VARCHAR | NOT NULL | Type name |
| description | VARCHAR | NULLABLE | Description |

**Relationships**:
- One-to-Many: `tools`

#### 5.3 tools
**Purpose**: Tool inventory

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| type | INTEGER | FK → tool_types(id), NOT NULL | Tool type |
| description | VARCHAR | NULLABLE | Description |
| hsl_part_number | VARCHAR | NULLABLE | HSL part number |
| quantity | FLOAT | NOT NULL | Quantity |
| status | INTEGER | FK → inventory_status(id), NOT NULL | Status |

**Foreign Keys**:
- `type` → `inventory.tool_types(id)`
- `status` → `inventory.inventory_status(id)`

#### 5.4 tool_usage
**Purpose**: Tool usage tracking

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| tool | INTEGER | FK → tools(id), NOT NULL | Tool reference |
| order_id | INTEGER | NOT NULL | Order ID |
| operator_id | INTEGER | NOT NULL | Operator ID |
| op_id | INTEGER | NOT NULL | Operation ID |
| quantity | FLOAT | NOT NULL | Quantity used |

**Foreign Keys**:
- `tool` → `inventory.tools(id)`

#### 5.5 instrument_types
**Purpose**: Instrument type definitions

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| name | VARCHAR | NOT NULL | Type name |
| description | VARCHAR | NULLABLE | Description |

**Relationships**:
- One-to-Many: `instruments`

#### 5.6 instruments
**Purpose**: Instrument inventory

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| type | INTEGER | FK → instrument_types(id), NOT NULL | Instrument type |
| description | VARCHAR | NULLABLE | Description |
| instrument_code | VARCHAR | NULLABLE | Instrument code |
| size | VARCHAR | NULLABLE | Size |
| equipment_number | VARCHAR | NULLABLE | Equipment number |
| maintenance_plan | VARCHAR | NULLABLE | Maintenance plan |
| notification_number | VARCHAR | NULLABLE | Notification number |
| calibration_date | TIMESTAMP | NULLABLE | Calibration date |
| calibration_due_date | TIMESTAMP | NULLABLE | Calibration due date |
| location | VARCHAR | NULLABLE | Location |
| quantity | FLOAT | NOT NULL | Quantity |
| status | INTEGER | FK → inventory_status(id), NOT NULL | Status |

**Foreign Keys**:
- `type` → `inventory.instrument_types(id)`
- `status` → `inventory.inventory_status(id)`

#### 5.7 jigs_fixtures
**Purpose**: Jigs and fixtures inventory

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| project_name | VARCHAR | NOT NULL | Project name |
| part_number | VARCHAR | NOT NULL | Part number |
| revision | VARCHAR | NULLABLE | Revision |
| description | VARCHAR | NULLABLE | Description |
| operation_number | INTEGER | NOT NULL | Operation number |
| fixture_number | VARCHAR | NOT NULL | Fixture number |
| status | INTEGER | FK → inventory_status(id), NOT NULL | Status |

**Foreign Keys**:
- `status` → `inventory.inventory_status(id)`

#### 5.8 units
**Purpose**: Unit of measure definitions

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| name | VARCHAR | NOT NULL | Unit name |

**Relationships**:
- One-to-Many: `raw_materials`, `spares_consumables`

#### 5.9 raw_materials
**Purpose**: Raw material inventory

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| child_part_number | VARCHAR | NOT NULL | Part number |
| description | VARCHAR | NULLABLE | Description |
| quantity | FLOAT | NOT NULL | Quantity |
| unit | INTEGER | FK → units(id), NOT NULL | Unit reference |
| status | INTEGER | FK → inventory_status(id), NOT NULL | Status |
| available_from | TIMESTAMP | NULLABLE | Available from date |

**Foreign Keys**:
- `unit` → `inventory.units(id)`
- `status` → `inventory.inventory_status(id)`

**Relationships**:
- One-to-Many: `orders` (reverse)

#### 5.10 spares_consumables
**Purpose**: Spares and consumables inventory

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| description | VARCHAR | NOT NULL | Description |
| unit | INTEGER | FK → units(id), NOT NULL | Unit reference |
| quantity | FLOAT | NOT NULL | Quantity |

**Foreign Keys**:
- `unit` → `inventory.units(id)`

---

### 6. inventoryv1 Schema

#### 6.1 categories
**Purpose**: Inventory main categories

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| name | VARCHAR | NOT NULL | Category name |
| description | VARCHAR | NULLABLE | Description |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| created_by | INTEGER | FK → users(id), NOT NULL | Creator reference |
| updated_at | TIMESTAMP | NULLABLE | Update timestamp |

**Foreign Keys**:
- `created_by` → `auth.users(id)`

**Relationships**:
- Many-to-One: `created_by` → `User`
- One-to-Many: `subcategories`

#### 6.2 subcategories
**Purpose**: Inventory sub-categories

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| category | INTEGER | FK → categories(id), NOT NULL | Category reference |
| name | VARCHAR | NOT NULL | Subcategory name |
| description | VARCHAR | NULLABLE | Description |
| dynamic_fields | JSON | NOT NULL | Dynamic field definitions |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| created_by | INTEGER | FK → users(id), NOT NULL | Creator reference |
| updated_at | TIMESTAMP | NULLABLE | Update timestamp |

**Foreign Keys**:
- `category` → `inventoryv1.categories(id)`
- `created_by` → `auth.users(id)`

**Relationships**:
- Many-to-One: `category` → `InventoryCategory`, `created_by` → `User`
- One-to-Many: `items`

#### 6.3 items
**Purpose**: Inventory items

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| subcategory | INTEGER | FK → subcategories(id), NOT NULL | Subcategory reference |
| item_code | VARCHAR | UNIQUE, NOT NULL | Item code |
| dynamic_data | JSON | NOT NULL | Dynamic field values |
| quantity | INTEGER | NOT NULL | Total quantity |
| available_quantity | INTEGER | NOT NULL | Available quantity |
| status | VARCHAR | NOT NULL | Status |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Update timestamp |
| created_by | INTEGER | FK → users(id), NOT NULL | Creator reference |

**Foreign Keys**:
- `subcategory` → `inventoryv1.subcategories(id)`
- `created_by` → `auth.users(id)`

**Relationships**:
- Many-to-One: `subcategory` → `InventorySubCategory`, `created_by` → `User`
- One-to-Many: `calibrations`, `transactions`, `requests`, `return_requests`, `connectivity`, `order_tools`

#### 6.4 calibration_schedules
**Purpose**: Calibration schedules

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| inventory_item | INTEGER | FK → items(id), NOT NULL | Inventory item reference |
| calibration_type | VARCHAR | NOT NULL | Calibration type |
| frequency_days | INTEGER | NOT NULL | Frequency in days |
| last_calibration | TIMESTAMP | NULLABLE | Last calibration date |
| next_calibration | TIMESTAMP | NOT NULL | Next calibration date |
| remarks | VARCHAR | NULLABLE | Remarks |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Update timestamp |
| created_by | INTEGER | FK → users(id), NOT NULL | Creator reference |

**Foreign Keys**:
- `inventory_item` → `inventoryv1.items(id)`
- `created_by` → `auth.users(id)`

**Relationships**:
- Many-to-One: `inventory_item` → `InventoryItem`, `created_by` → `User`
- One-to-Many: `calibration_history`, `notification`

#### 6.5 calibration_history
**Purpose**: Calibration history

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| calibration_schedule | INTEGER | FK → calibration_schedules(id), NOT NULL | Schedule reference |
| calibration_date | TIMESTAMP | NOT NULL | Calibration date |
| performed_by | INTEGER | FK → users(id), NOT NULL | Performed by user |
| result | VARCHAR | NOT NULL | Result (Pass/Fail) |
| certificate_number | VARCHAR | NULLABLE | Certificate number |
| remarks | VARCHAR | NULLABLE | Remarks |
| next_due_date | TIMESTAMP | NOT NULL | Next due date |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Update timestamp |

**Foreign Keys**:
- `calibration_schedule` → `inventoryv1.calibration_schedules(id)`
- `performed_by` → `auth.users(id)`

#### 6.6 requests
**Purpose**: Inventory issue requests

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| inventory_item | INTEGER | FK → items(id), NOT NULL | Inventory item reference |
| requested_by | INTEGER | FK → users(id), NOT NULL | Requestor reference |
| order | INTEGER | FK → orders(id), NOT NULL | Order reference |
| operation | INTEGER | FK → operations(id), NULLABLE | Operation reference |
| quantity | INTEGER | NOT NULL | Requested quantity |
| purpose | VARCHAR | NOT NULL | Purpose |
| status | VARCHAR | NOT NULL | Status |
| approved_by | INTEGER | FK → users(id), NULLABLE | Approver reference |
| approved_at | TIMESTAMP | NULLABLE | Approval timestamp |
| expected_return_date | TIMESTAMP | NOT NULL | Expected return date |
| actual_return_date | TIMESTAMP | NULLABLE | Actual return date |
| remarks | VARCHAR | NULLABLE | Remarks |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Update timestamp |

**Foreign Keys**:
- `inventory_item` → `inventoryv1.items(id)`
- `requested_by` → `auth.users(id)`
- `order` → `master_order.orders(id)`
- `operation` → `master_order.operations(id)`
- `approved_by` → `auth.users(id)`

**Relationships**:
- Many-to-One: `inventory_item` → `InventoryItem`, `requested_by` → `User` (inventory_requests), `order` → `Order`, `approved_by` → `User` (approved_inventory_requests)
- One-to-Many: `transactions`, `return_requests`

#### 6.7 return_requests
**Purpose**: Inventory return requests

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| inventory_item | INTEGER | FK → items(id), NOT NULL | Inventory item reference |
| original_request | INTEGER | FK → requests(id), NOT NULL | Original request reference |
| requested_by | INTEGER | FK → users(id), NOT NULL | Requestor reference |
| quantity_to_return | INTEGER | NOT NULL | Quantity to return |
| return_reason | VARCHAR | NOT NULL | Return reason |
| status | VARCHAR | NOT NULL | Status |
| approved_by | INTEGER | FK → users(id), NULLABLE | Approver reference |
| approved_at | TIMESTAMP | NULLABLE | Approval timestamp |
| actual_return_date | TIMESTAMP | NULLABLE | Actual return date |
| remarks | VARCHAR | NULLABLE | Remarks |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Update timestamp |

**Foreign Keys**:
- `inventory_item` → `inventoryv1.items(id)`
- `original_request` → `inventoryv1.requests(id)`
- `requested_by` → `auth.users(id)`
- `approved_by` → `auth.users(id)`

**Relationships**:
- Many-to-One: `inventory_item` → `InventoryItem`, `original_request` → `InventoryRequest`, `requested_by` → `User` (inventory_return_requests), `approved_by` → `User` (approved_inventory_return_requests)
- One-to-Many: `transactions`

#### 6.8 transactions
**Purpose**: Inventory transaction log

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| inventory_item | INTEGER | FK → items(id), NOT NULL | Inventory item reference |
| transaction_type | VARCHAR | NOT NULL | Transaction type |
| quantity | INTEGER | NOT NULL | Quantity |
| quantity_before | INTEGER | NOT NULL | Quantity before |
| quantity_after | INTEGER | NOT NULL | Quantity after |
| reference_request | INTEGER | FK → requests(id), NULLABLE | Reference request |
| reference_return_request | INTEGER | FK → return_requests(id), NULLABLE | Reference return request |
| performed_by | INTEGER | FK → users(id), NOT NULL | Performed by user |
| remarks | VARCHAR | NULLABLE | Remarks |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| transaction_reference | VARCHAR | NULLABLE | Transaction reference |
| location_from | VARCHAR | NULLABLE | From location |
| location_to | VARCHAR | NULLABLE | To location |

**Foreign Keys**:
- `inventory_item` → `inventoryv1.items(id)`
- `reference_request` → `inventoryv1.requests(id)`
- `reference_return_request` → `inventoryv1.return_requests(id)`
- `performed_by` → `auth.users(id)`

---

### 7. quality Schema

#### 7.1 master_boc
**Purpose**: Bill of Characteristics

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| part_number | VARCHAR | NOT NULL | Part number |
| nominal | VARCHAR | NOT NULL | Nominal value |
| uppertol | FLOAT | NOT NULL | Upper tolerance |
| lowertol | FLOAT | NOT NULL | Lower tolerance |
| zone | VARCHAR | NOT NULL | Zone |
| dimension_type | VARCHAR | NOT NULL | Dimension type |
| measured_instrument | VARCHAR | NOT NULL | Measurement instrument |
| op_no | INTEGER | NOT NULL | Operation number |
| bbox | VARCHAR | NOT NULL | Bounding box |
| ipid | VARCHAR | NOT NULL | IPID |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |

#### 7.2 stage_inspection
**Purpose**: Stage inspection measurements

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| op_id | INTEGER | NOT NULL | Operation ID |
| nominal_value | VARCHAR | NOT NULL | Nominal value |
| uppertol | FLOAT | NOT NULL | Upper tolerance |
| lowertol | FLOAT | NOT NULL | Lower tolerance |
| zone | VARCHAR | NOT NULL | Zone |
| dimension_type | VARCHAR | NOT NULL | Dimension type |
| measured_1 | VARCHAR | NOT NULL | Measurement 1 |
| measured_2 | VARCHAR | NOT NULL | Measurement 2 |
| measured_3 | VARCHAR | NOT NULL | Measurement 3 |
| measured_mean | VARCHAR | NOT NULL | Mean measurement |
| measured_instrument | VARCHAR | NOT NULL | Instrument used |
| used_inst | VARCHAR | NOT NULL | Used instrument |
| op_no | INTEGER | NOT NULL | Operation number |
| order_id | INTEGER | NOT NULL | Order ID |
| quantity_no | INTEGER | NULLABLE | Quantity number |
| bbox | VARCHAR | NULLABLE | Bounding box |
| is_done | BOOLEAN | NOT NULL, DEFAULT FALSE | Completion status |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |

#### 7.3 connectivity
**Purpose**: Instrument connectivity

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| inventory_item | INTEGER | FK → items(id), NOT NULL | Inventory item reference |
| instrument | VARCHAR | NOT NULL | Instrument |
| uuid | VARCHAR | NOT NULL | UUID |
| address | VARCHAR | NOT NULL | Address |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |

**Foreign Keys**:
- `inventory_item` → `inventoryv1.items(id)`

#### 7.4 ftp_status
**Purpose**: IPID completion tracking

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| order_id | BIGINT | NOT NULL | Order ID |
| ipid | VARCHAR(255) | NOT NULL | IPID |
| is_completed | BOOLEAN | NOT NULL, DEFAULT FALSE | Completion status |
| Status | VARCHAR(255) | NOT NULL | Status |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Update timestamp |

**Composite Key**: (order_id, ipid)

---

### 8. document_management Schema

#### 8.1 doc_folders
**Purpose**: Document folders (v1)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| parent_folder | INTEGER | NULLABLE | Parent folder ID |
| folder_name | VARCHAR | NOT NULL | Folder name |
| folder_path | VARCHAR | UNIQUE, NOT NULL | Folder path |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| created_by | INTEGER | FK → users(id), NOT NULL | Creator reference |
| is_active | BOOLEAN | NOT NULL, DEFAULT TRUE | Active status |

**Foreign Keys**:
- `created_by` → `auth.users(id)`

**Relationships**:
- Many-to-One: `created_by` → `User`
- One-to-Many: `documents`

#### 8.2 doc_types
**Purpose**: Document types (v1)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| type_name | VARCHAR | UNIQUE, NOT NULL | Type name |
| description | VARCHAR | NULLABLE | Description |
| file_extensions | JSON | NOT NULL | Allowed extensions |
| is_active | BOOLEAN | NOT NULL, DEFAULT TRUE | Active status |

**Relationships**:
- One-to-Many: `documents`

#### 8.3 documents
**Purpose**: Documents (v1)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| folder | INTEGER | FK → doc_folders(id), NOT NULL | Folder reference |
| part_number_id | INTEGER | FK → orders(id), NOT NULL | Part number/Order reference |
| doc_type | INTEGER | FK → doc_types(id), NOT NULL | Document type |
| document_name | VARCHAR | NOT NULL | Document name |
| description | VARCHAR | NULLABLE | Description |
| minio_path | VARCHAR | NOT NULL | MinIO path |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| created_by | INTEGER | FK → users(id), NOT NULL | Creator reference |
| is_active | BOOLEAN | NOT NULL, DEFAULT TRUE | Active status |
| latest_version | INTEGER | FK → document_versions(id), NULLABLE | Latest version reference |

**Foreign Keys**:
- `folder` → `document_management.doc_folders(id)`
- `part_number_id` → `master_order.orders(id)`
- `doc_type` → `document_management.doc_types(id)`
- `created_by` → `auth.users(id)`
- `latest_version` → `document_management.document_versions(id)`

**Relationships**:
- Many-to-One: `folder` → `DocFolder`, `part_number_id` → `Order`, `doc_type` → `DocType`, `created_by` → `User`, `latest_version` → `DocumentVersion`
- One-to-Many: `versions`, `access_logs`, `mpps`

#### 8.4 document_versions
**Purpose**: Document versions (v1)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| document | INTEGER | FK → documents(id), NOT NULL | Document reference |
| latest_of | INTEGER | FK → documents(id), NULLABLE | Latest version flag |
| version_number | VARCHAR | NOT NULL | Version number |
| minio_object_id | VARCHAR | NOT NULL | MinIO object ID |
| file_size | INTEGER | NOT NULL | File size |
| checksum | VARCHAR | NOT NULL | Checksum |
| metadata | JSON | NULLABLE | Metadata |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| created_by | INTEGER | FK → users(id), NOT NULL | Creator reference |
| status | VARCHAR | NOT NULL | Status |

**Foreign Keys**:
- `document` → `document_management.documents(id)`
- `latest_of` → `document_management.documents(id)`
- `created_by` → `auth.users(id)`

**Relationships**:
- Many-to-One: `document` → `Document`, `latest_of` → `Document`, `created_by` → `User`
- One-to-Many: `access_logs`

#### 8.5 document_access_logs
**Purpose**: Document access logs (v1)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| document | INTEGER | FK → documents(id), NULLABLE | Document reference |
| version | INTEGER | FK → document_versions(id), NULLABLE | Version reference |
| user | INTEGER | FK → users(id), NOT NULL | User reference |
| action_type | VARCHAR | NOT NULL | Action type |
| action_timestamp | TIMESTAMP | NOT NULL, DEFAULT NOW | Action timestamp |
| ip_address | VARCHAR | NULLABLE | IP address |

**Foreign Keys**:
- `document` → `document_management.documents(id)`
- `version` → `document_management.document_versions(id)`
- `user` → `auth.users(id)`

---

### 9. document_management_v2 Schema

#### 9.1 folders
**Purpose**: Folders (v2)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| name | VARCHAR | NOT NULL | Folder name |
| path | VARCHAR | UNIQUE, NOT NULL | Folder path |
| parent_folder_id | INTEGER | FK → folders(id), NULLABLE | Parent folder |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| created_by_id | INTEGER | FK → users(id), NOT NULL | Creator reference |
| is_active | BOOLEAN | NOT NULL, DEFAULT TRUE | Active status |

**Foreign Keys**:
- `parent_folder_id` → `document_management_v2.folders(id)` (self-referential)
- `created_by_id` → `auth.users(id)`

**Relationships**:
- Many-to-One: `parent_folder_id` → `FolderV2` (self-referential), `created_by_id` → `User`
- One-to-Many: `child_folders`, `documents`

#### 9.2 document_types
**Purpose**: Document types (v2)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| name | VARCHAR | UNIQUE, NOT NULL | Type name |
| description | VARCHAR | NULLABLE | Description |
| allowed_extensions | JSON | NOT NULL | Allowed file extensions |
| is_active | BOOLEAN | NOT NULL, DEFAULT TRUE | Active status |

**Relationships**:
- One-to-Many: `documents`

#### 9.3 documents
**Purpose**: Documents (v2)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| name | VARCHAR | NOT NULL | Document name |
| folder_id_v2 | INTEGER | FK → folders(id), NOT NULL | Folder reference |
| doc_type_id_v2 | INTEGER | FK → document_types(id), NOT NULL | Document type |
| description | VARCHAR | NULLABLE | Description |
| part_number | VARCHAR | NULLABLE | Part number string |
| production_order_id_v2 | INTEGER | FK → orders(id), NULLABLE | Production order reference |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| created_by_id_v2 | INTEGER | FK → users(id), NOT NULL | Creator reference |
| is_active | BOOLEAN | NOT NULL, DEFAULT TRUE | Active status |
| latest_version_id_v2 | INTEGER | FK → document_versions(id), NULLABLE | Latest version |

**Foreign Keys**:
- `folder_id_v2` → `document_management_v2.folders(id)`
- `doc_type_id_v2` → `document_management_v2.document_types(id)`
- `production_order_id_v2` → `master_order.orders(id)`
- `created_by_id_v2` → `auth.users(id)`
- `latest_version_id_v2` → `document_management_v2.document_versions(id)`

**Relationships**:
- Many-to-One: `folder` → `FolderV2`, `doc_type` → `DocumentTypeV2`, `production_order` → `Order`, `created_by` → `User`, `latest_version` → `DocumentVersionV2`
- One-to-Many: `versions`, `access_logs`

#### 9.4 document_versions
**Purpose**: Document versions (v2)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| document_id_v2 | INTEGER | FK → documents(id), NOT NULL | Document reference |
| latest_of_id_v2 | INTEGER | FK → documents(id), NULLABLE | Latest version flag |
| version_number | VARCHAR | NOT NULL | Version number |
| minio_path | VARCHAR | UNIQUE, NOT NULL | MinIO path |
| file_size | INTEGER | NOT NULL | File size |
| checksum | VARCHAR | NOT NULL | Checksum |
| metadata | JSON | NULLABLE | Metadata |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| created_by_id_v2 | INTEGER | FK → users(id), NOT NULL | Creator reference |
| is_active | BOOLEAN | NOT NULL, DEFAULT TRUE | Active status |

**Foreign Keys**:
- `document_id_v2` → `document_management_v2.documents(id)`
- `latest_of_id_v2` → `document_management_v2.documents(id)`
- `created_by_id_v2` → `auth.users(id)`

**Relationships**:
- Many-to-One: `document` → `DocumentV2`, `latest_of` → `DocumentV2`, `created_by` → `User`
- One-to-Many: `access_logs`

#### 9.5 document_access_logs
**Purpose**: Document access logs (v2)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| document_id_v2 | INTEGER | FK → documents(id), NOT NULL | Document reference |
| version_id_v2 | INTEGER | FK → document_versions(id), NULLABLE | Version reference |
| user_id_v2 | INTEGER | FK → users(id), NOT NULL | User reference |
| action_type | VARCHAR | NOT NULL | Action type |
| action_timestamp | TIMESTAMP | NOT NULL, DEFAULT NOW | Action timestamp |
| ip_address | VARCHAR | NULLABLE | IP address |

**Foreign Keys**:
- `document_id_v2` → `document_management_v2.documents(id)`
- `version_id_v2` → `document_management_v2.document_versions(id)`
- `user_id_v2` → `auth.users(id)`

---

### 10. logs Schema

#### 10.1 machine_status_logs
**Purpose**: Machine status logging

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| machine_id | INTEGER | NOT NULL | Machine ID |
| machine_make | VARCHAR | NOT NULL | Machine make |
| status_name | VARCHAR | NOT NULL | Status name |
| description | VARCHAR | NULLABLE | Description |
| updated_at | TIMESTAMP | NOT NULL | Update timestamp |
| created_by | VARCHAR | NULLABLE | Creator |
| is_acknowledged | BOOLEAN | NOT NULL, DEFAULT FALSE | Acknowledged status |
| acknowledged_by | VARCHAR | NULLABLE | Acknowledged by |
| acknowledged_at | TIMESTAMP | NULLABLE | Acknowledgment time |
| read | BOOLEAN | DEFAULT FALSE | Read status |

#### 10.2 raw_material_status_logs
**Purpose**: Raw material status logging

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| material_id | INTEGER | NOT NULL | Material ID |
| part_number | VARCHAR | NULLABLE | Part number |
| status_name | VARCHAR | NOT NULL | Status name |
| description | VARCHAR | NULLABLE | Description |
| updated_at | TIMESTAMP | NOT NULL | Update timestamp |
| created_by | VARCHAR | NULLABLE | Creator |
| is_acknowledged | BOOLEAN | NOT NULL, DEFAULT FALSE | Acknowledged status |
| acknowledged_by | VARCHAR | NULLABLE | Acknowledged by |
| acknowledged_at | TIMESTAMP | NULLABLE | Acknowledgment time |
| read | BOOLEAN | DEFAULT FALSE | Read status |

#### 10.3 pokayoke_checklists
**Purpose**: PokaYoke checklist templates

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| name | VARCHAR | NOT NULL | Checklist name |
| description | VARCHAR | NULLABLE | Description |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |
| created_by | VARCHAR | NOT NULL | Creator |
| is_active | BOOLEAN | NOT NULL, DEFAULT TRUE | Active status |

**Relationships**:
- One-to-Many: `items`, `machine_assignments`, `completed_logs`

#### 10.4 pokayoke_checklist_items
**Purpose**: PokaYoke checklist items

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| checklist | INTEGER | FK → pokayoke_checklists(id), NOT NULL | Checklist reference |
| item_text | VARCHAR | NOT NULL | Item text |
| sequence_number | INTEGER | NOT NULL | Sequence number |
| item_type | VARCHAR | NOT NULL | Item type |
| is_required | BOOLEAN | NOT NULL, DEFAULT TRUE | Required status |
| expected_value | VARCHAR | NULLABLE | Expected value |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |

**Foreign Keys**:
- `checklist` → `logs.pokayoke_checklists(id)`

#### 10.5 pokayoke_machine_assignments
**Purpose**: PokaYoke machine assignments

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| checklist | INTEGER | FK → pokayoke_checklists(id), NOT NULL | Checklist reference |
| machine_id | INTEGER | NOT NULL | Machine ID |
| machine_make | VARCHAR | NULLABLE | Machine make |
| assigned_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Assigned timestamp |
| assigned_by | VARCHAR | NOT NULL | Assigned by |
| is_active | BOOLEAN | NOT NULL, DEFAULT TRUE | Active status |

**Foreign Keys**:
- `checklist` → `logs.pokayoke_checklists(id)`

#### 10.6 pokayoke_completed_logs
**Purpose**: PokaYoke completion logs

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| checklist | INTEGER | FK → pokayoke_checklists(id), NOT NULL | Checklist reference |
| machine_id | INTEGER | NOT NULL | Machine ID |
| operator_id | VARCHAR | NOT NULL | Operator ID |
| production_order | VARCHAR | NULLABLE | Production order |
| part_number | VARCHAR | NULLABLE | Part number |
| completed_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Completion timestamp |
| all_items_passed | BOOLEAN | NOT NULL | All items passed |
| comments | VARCHAR | NULLABLE | Comments |
| read | BOOLEAN | DEFAULT FALSE | Read status |

**Foreign Keys**:
- `checklist` → `logs.pokayoke_checklists(id)`

**Relationships**:
- Many-to-One: `checklist` → `PokaYokeChecklist`
- One-to-Many: `item_responses`

#### 10.7 pokayoke_item_responses
**Purpose**: PokaYoke item responses

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| completed_log | INTEGER | FK → pokayoke_completed_logs(id), NOT NULL | Completed log reference |
| item_id | INTEGER | NOT NULL | Item ID |
| item_text | VARCHAR | NOT NULL | Item text |
| response_value | VARCHAR | NOT NULL | Response value |
| is_conforming | BOOLEAN | NOT NULL | Conforming status |
| timestamp | TIMESTAMP | NOT NULL, DEFAULT NOW | Timestamp |

**Foreign Keys**:
- `completed_log` → `logs.pokayoke_completed_logs(id)`

#### 10.8 machine_calibration_logs
**Purpose**: Machine calibration logs with notification trigger

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| timestamp | TIMESTAMP | NOT NULL, DEFAULT NOW | Timestamp |
| calibration_due_date | DATE | NULLABLE | Calibration due date |
| machine_id | INTEGER | FK → machines(id), NULLABLE | Machine reference |
| read | BOOLEAN | DEFAULT FALSE | Read status |

**Foreign Keys**:
- `machine_id` → `master_order.machines(id)`

**Special**: Has `after_insert()` hook that triggers notifications

#### 10.9 instrument_calibration_logs
**Purpose**: Instrument calibration logs with notification trigger

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| timestamp | TIMESTAMP | NOT NULL, DEFAULT NOW | Timestamp |
| calibration_due_date | DATE | NULLABLE | Calibration due date |
| instrument_id | INTEGER | FK → calibration_schedules(id), NULLABLE | Instrument reference |
| read | BOOLEAN | DEFAULT FALSE | Read status |

**Foreign Keys**:
- `instrument_id` → `inventoryv1.calibration_schedules(id)`

**Special**: Has `after_insert()` hook that triggers notifications

#### 10.10 asset_logs
**Purpose**: Asset logging

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| machine_name | VARCHAR | NOT NULL | Machine name |
| from_time | TIMESTAMP | NOT NULL | From time |
| to_time | TIMESTAMP | NOT NULL | To time |
| status | VARCHAR | NOT NULL | Status |
| remarks | VARCHAR | NULLABLE | Remarks |
| created_at | TIMESTAMP | NOT NULL, DEFAULT NOW | Creation timestamp |

---

### 11. EMS Schema

#### 11.1 machine_ems_history
**Purpose**: Historical EMS data

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| machine_id | INTEGER | NOT NULL | Machine ID |
| timestamp | TIMESTAMP | NOT NULL, DEFAULT NOW | Timestamp |
| phase_a_voltage | FLOAT | NULLABLE | Phase A voltage |
| phase_b_voltage | FLOAT | NULLABLE | Phase B voltage |
| phase_c_voltage | FLOAT | NULLABLE | Phase C voltage |
| avg_phase_voltage | FLOAT | NULLABLE | Average phase voltage |
| line_ab_voltage | FLOAT | NULLABLE | Line AB voltage |
| line_bc_voltage | FLOAT | NULLABLE | Line BC voltage |
| line_ca_voltage | FLOAT | NULLABLE | Line CA voltage |
| avg_line_voltage | FLOAT | NULLABLE | Average line voltage |
| phase_a_current | FLOAT | NULLABLE | Phase A current |
| phase_b_current | FLOAT | NULLABLE | Phase B current |
| phase_c_current | FLOAT | NULLABLE | Phase C current |
| avg_three_phase_current | FLOAT | NULLABLE | Average three-phase current |
| power_factor | FLOAT | NULLABLE | Power factor |
| frequency | FLOAT | NULLABLE | Frequency |
| total_instantaneous_power | FLOAT | NULLABLE | Total instantaneous power |
| active_energy_delivered | FLOAT | NULLABLE | Active energy delivered |

**Foreign Keys**:
- `machine_id` → `master_order.machines(id)`

**Special**: Uses SQL constraints directly

#### 11.2 machine_ems_live
**Purpose**: Live EMS data (one row per machine)

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| machine_id | INTEGER | PK, UNIQUE | Machine ID |
| timestamp | TIMESTAMP | NOT NULL, DEFAULT NOW | Timestamp |
| phase_a_voltage | FLOAT | NULLABLE | Phase A voltage |
| phase_b_voltage | FLOAT | NULLABLE | Phase B voltage |
| phase_c_voltage | FLOAT | NULLABLE | Phase C voltage |
| avg_phase_voltage | FLOAT | NULLABLE | Average phase voltage |
| line_ab_voltage | FLOAT | NULLABLE | Line AB voltage |
| line_bc_voltage | FLOAT | NULLABLE | Line BC voltage |
| line_ca_voltage | FLOAT | NULLABLE | Line CA voltage |
| avg_line_voltage | FLOAT | NULLABLE | Average line voltage |
| phase_a_current | FLOAT | NULLABLE | Phase A current |
| phase_b_current | FLOAT | NULLABLE | Phase B current |
| phase_c_current | FLOAT | NULLABLE | Phase C current |
| avg_three_phase_current | FLOAT | NULLABLE | Average three-phase current |
| power_factor | FLOAT | NULLABLE | Power factor |
| frequency | FLOAT | NULLABLE | Frequency |
| total_instantaneous_power | FLOAT | NULLABLE | Total instantaneous power |
| active_energy_delivered | FLOAT | NULLABLE | Active energy delivered |
| status | INTEGER | NULLABLE | Status |

**Foreign Keys**:
- `machine_id` → `master_order.machines(id)`

#### 11.3 shiftwise_energy_live
**Purpose**: Live shift-wise energy data

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| timestamp | TIMESTAMP | NOT NULL, DEFAULT NOW | Timestamp |
| first_shift | FLOAT | NOT NULL | First shift energy |
| second_shift | FLOAT | NOT NULL | Second shift energy |
| third_shift | FLOAT | NOT NULL | Third shift energy |
| total_energy | FLOAT | NOT NULL | Total energy |
| machine_id | INTEGER | NOT NULL | Machine ID |

**Foreign Keys**:
- `machine_id` → `master_order.machines(id)`

#### 11.4 shiftwise_energy_history
**Purpose**: Historical shift-wise energy data

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| timestamp | TIMESTAMP | NOT NULL, DEFAULT NOW | Timestamp |
| first_shift | FLOAT | NOT NULL | First shift energy |
| second_shift | FLOAT | NOT NULL | Second shift energy |
| third_shift | FLOAT | NOT NULL | Third shift energy |
| total_energy | FLOAT | NOT NULL | Total energy |
| machine_id | INTEGER | NOT NULL | Machine ID |

**Foreign Keys**:
- `machine_id` → `master_order.machines(id)`

---

### 12. hr_schema

#### 12.1 employees
**Purpose**: Employee records

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| name | VARCHAR | NOT NULL | Employee name |
| email | VARCHAR | UNIQUE, NOT NULL | Email |
| department | VARCHAR | NOT NULL | Department |

**Relationships**:
- One-to-Many: `salary_records`

---

### 13. finance_schema

#### 13.1 salary_records
**Purpose**: Salary records

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PK, AUTO | Primary key |
| employee | INTEGER | FK → employees(id), NOT NULL | Employee reference |
| amount | FLOAT | NOT NULL | Amount |
| payment_date | DATE | NOT NULL | Payment date |
| bonus | FLOAT | NULLABLE | Bonus |

**Foreign Keys**:
- `employee` → `hr_schema.employees(id)`

---

## Relationship Summary

### Core Relationships

1. **User-centric**:
   - Users → Orders (created orders)
   - Users → Documents (created documents)
   - Users → Inventory (created items, requests)
   - Users → Production logs (operator)
   - Users → Reschedule history (rescheduled by)

2. **Order-centric**:
   - Orders → Operations → Programs
   - Orders → Planned schedule items
   - Orders → Production logs
   - Orders → Inventory requests
   - Orders → Documents

3. **Machine-centric**:
   - Machines → Operations (assigned to)
   - Machines → Production data (raw, live)
   - Machines → Shift summaries
   - Machines → Calibration logs
   - Machines → EMS data

4. **Document-centric**:
   - Folders → Documents → Versions
   - Documents → Orders (part numbers)
   - Documents → Access logs

5. **Inventory-centric**:
   - Categories → Subcategories → Items
   - Items → Calibration schedules
   - Items → Requests → Transactions
   - Items → Connectivity (quality instruments)

---

## Database Statistics

- **Schemas**: 13
- **Total Tables**: ~75
- **Foreign Keys**: 100+
- **Relationships**: Complex many-to-one and one-to-many
- **Database**: PostgreSQL
- **ORM**: Pony ORM

---

## Notes

- All timestamps use UTC
- Composite keys used where appropriate
- Many JSON fields for flexible data storage
- Special hooks for notification triggers
- Some tables use direct SQL constraints
- Self-referential relationships in FolderV2

---

**Document Version**: 1.0  
**Last Updated**: 2025


