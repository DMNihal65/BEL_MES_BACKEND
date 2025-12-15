# BEL MES BACKEND - Complete Structure Documentation

## 📋 Table of Contents
1. [Overview](#overview)
2. [Project Structure](#project-structure)
3. [Architecture Components](#architecture-components)
4. [API Endpoints](#api-endpoints)
5. [Database Models](#database-models)
6. [Key Features](#key-features)
7. [Deployment](#deployment)

---

## 🎯 Overview

**BEL MES (Manufacturing Execution System) Backend** is a FastAPI-based Python application that manages:
- Production planning and scheduling
- Real-time production monitoring
- Quality control
- Inventory management
- Document management
- Energy monitoring
- Operator logging and authentication

**Technology Stack:**
- **Framework**: FastAPI
- **ORM**: Pony ORM
- **Database**: PostgreSQL
- **File Storage**: MinIO
- **Authentication**: JWT (JSON Web Tokens)
- **Deployment**: Docker, Kubernetes

---

## 📁 Project Structure

```
BEL_MES_BACKEND/
│
├── app/                          # Main application directory
│   ├── main.py                   # FastAPI application entry point
│   │
│   ├── algorithm/                # Scheduling algorithms
│   │   ├── scheduling.py         # Main scheduling algorithm
│   │   ├── planned_scheduling.py # Planned schedule generation
│   │   └── scheduling_copy.py    # Backup/alternative scheduling
│   │
│   ├── api/                      # API endpoints
│   │   └── v1/
│   │       └── endpoints/        # API endpoint modules
│   │           ├── auth.py              # Authentication endpoints
│   │           ├── operator_login.py    # Operator login/logout
│   │           ├── planning.py          # Production planning
│   │           ├── mpp.py               # Master Production Planning
│   │           ├── operations.py        # Operation management
│   │           ├── scheduled.py         # Schedule management
│   │           ├── scheduled2.py        # Extended scheduling
│   │           ├── priority_scheduling.py       # Priority-based scheduling
│   │           ├── dynamic_rescheduling.py     # Dynamic rescheduling
│   │           ├── production_logs.py          # Production logging
│   │           ├── production_monitoring.py    # Real-time monitoring
│   │           ├── component_status.py         # Component status
│   │           ├── comp_maintainance.py       # Component maintenance
│   │           ├── comp_operator.py           # Component operators
│   │           ├── programs.py               # Program management
│   │           ├── toolsprograms.py           # Tool programs
│   │           ├── pdc.py                    # Production Data Collection
│   │           ├── operator_log.py           # Operator logging
│   │           ├── operatorlog2.py          # Extended operator logs
│   │           ├── daily_production.py       # Daily production data
│   │           ├── quality.py               # Quality control
│   │           ├── inventoryv1.py           # Inventory management
│   │           ├── document_management.py     # Document management (v1)
│   │           ├── document_management_v2.py # Document management (v2)
│   │           ├── notification_service.py   # Notification system
│   │           ├── simple_notifications.py  # Simple notifications
│   │           ├── mttr_mtbf.py             # MTTR/MTBF calculations
│   │           ├── energymonitoring.py       # Energy monitoring
│   │           └── newlogs.py               # New logging system
│   │
│   ├── models/                   # Database models (Pony ORM)
│   │   ├── __init__.py          # Model imports
│   │   ├── user.py              # User and authentication models
│   │   ├── master_order.py      # Order and project models
│   │   ├── production.py        # Production and machine models
│   │   ├── scheduled.py         # Scheduling models
│   │   ├── inventory.py         # Inventory models
│   │   ├── quality.py           # Quality control models
│   │   ├── document_management.py     # Document models (v1)
│   │   ├── document_management_v2.py  # Document models (v2)
│   │   ├── logs.py              # Logging models
│   │   ├── hr_models.py         # HR models
│   │   ├── finance_models.py    # Finance models
│   │   └── energymonitoring.py  # Energy monitoring models
│   │
│   ├── schemas/                  # Pydantic schemas for validation
│   │   ├── user.py              # User schemas
│   │   ├── master_order_schemas.py
│   │   ├── quality.py
│   │   ├── inventoryv1.py
│   │   ├── document_schemas.py
│   │   ├── document_management_v2.py
│   │   ├── component_status.py
│   │   ├── component_quantities.py
│   │   ├── comp_maintainance.py
│   │   ├── daily_production.py
│   │   ├── pdc.py
│   │   ├── operations.py
│   │   ├── planning.py
│   │   ├── scheduled.py
│   │   ├── scheduled1.py
│   │   ├── mpp.py
│   │   ├── mttr_mtbf.py
│   │   ├── leadtime.py
│   │   ├── raw_material.py
│   │   ├── toolsprograms.py
│   │   ├── pokayoke.py
│   │   └── energymonitoring.py
│   │
│   ├── crud/                     # CRUD operations
│   │   ├── user.py              # User CRUD
│   │   ├── operation.py         # Operation CRUD
│   │   ├── quality.py           # Quality CRUD
│   │   ├── raw_material.py      # Raw material CRUD
│   │   ├── component_quantities.py
│   │   ├── leadtime.py
│   │   └── pdc.py
│   │
│   ├── routes/                   # Route modules
│   │   ├── master_order_routes.py
│   │   ├── finance_routes.py
│   │   ├── hr_routes.py
│   │   └── pokayoke.py
│   │
│   ├── services/                  # External services
│   │   ├── minio_service.py     # MinIO file storage service
│   │   ├── oee_collector_opcua.py  # OPC-UA data collection
│   │   └── oee_collector_ems.py    # EMS data collection
│   │
│   ├── utils/                     # Utility functions
│   │   └── production_calculations.py
│   │
│   ├── database/                 # Database configuration
│   │   ├── connection.py        # Database connection
│   │   ├── init_db.py           # Database initialization
│   │   └── migrations.py        # Database migrations
│   │
│   ├── core/                     # Core functionality
│   │   └── security.py          # Security utilities (JWT, password hashing)
│   │
│   ├── config/                    # Configuration
│   │   └── settings.py          # Application settings
│   │
│   └── simulator/                # Data simulation
│       ├── main.py
│       ├── test.py
│       ├── planned_schedule_items.csv
│       └── programs.csv
│
├── data/                         # Data files
│   └── priority_history.json
│
├── docker-compose.yml            # Docker Compose configuration
├── dockerfile                    # Docker image definition
├── requirements.txt              # Python dependencies
├── ENV@bel                       # Environment configuration
│
├── init-db.sql                   # Database initialization script
└── README.md                     # Project documentation
```

---

## 🏗️ Architecture Components

### 1. **Application Entry Point**
- **File**: `app/main.py`
- **Purpose**: FastAPI application initialization
- **Features**:
  - CORS middleware configuration
  - Database connection on startup
  - API router registration
  - Performance monitoring middleware
  - Health check endpoint

### 2. **Database Layer**
- **ORM**: Pony ORM
- **Database**: PostgreSQL
- **Schemas**: 
  - `auth` - Authentication and users
  - `master_order` - Orders and projects
  - `production` - Production data
  - `scheduling` - Scheduling data
  - `quality` - Quality control
  - `inventoryv1` - Inventory management
  - `document_management` - Document v1
  - `document_management_v2` - Document v2
  - `logs` - Logging and notifications
  - `EMS` - Energy monitoring
  - `hr_schema` - HR data
  - `finance_schema` - Finance data

### 3. **API Layer**
- **Version**: v1
- **Pattern**: RESTful API
- **Documentation**: Swagger UI at `/docs`

### 4. **Security**
- **Authentication**: JWT tokens
- **Password Hashing**: bcrypt
- **File**: `app/core/security.py`

### 5. **File Storage**
- **Service**: MinIO
- **File**: `app/services/minio_service.py`
- **Bucket**: documents

---

## 🔌 API Endpoints

### Authentication & User Management
- `/auth/login` - User login
- `/auth/logout` - User logout
- `/auth/register` - User registration
- `/auth/refresh` - Refresh token

### Operator Management
- `/operator-login` - Operator login
- `/operator-log` - Operator log entries
- `/operator-log2` - Extended operator logs

### Production Planning
- `/planning` - Production planning endpoints
- `/mpp` - Master Production Planning
- `/operations` - Operation management
- `/scheduled` - Schedule management
- `/scheduled2` - Extended scheduling

### Production Monitoring
- `/production-monitoring` - Real-time production monitoring
- `/production-logs` - Production logging
- `/daily-production` - Daily production data
- `/component-status` - Machine component status

### Quality Control
- `/quality` - Quality inspection endpoints
- Quality data management

### Scheduling & Rescheduling
- `/priority-scheduling` - Priority-based scheduling
- `/dynamic-rescheduling` - Dynamic job rescheduling

### Inventory Management
- `/inventory` - Inventory operations
- Material tracking
- Calibration schedules

### Document Management
- `/document-management` (v1) - Document management legacy
- `/api/v1/document-management` (v2) - Enhanced document management
- File upload/download
- Version control

### Programs & Tools
- `/programs` - CNC program management
- `/toolsprograms` - Tool program management

### Component Management
- `/comp-maintainance` - Component maintenance
- `/comp-operator` - Component operator assignments

### Notifications
- `/notifications` - Notification service
- `/simple-notifications` - Simple notification endpoints

### Logging & Monitoring
- `/newlogs` - New logging system
- `/mttr-mtbf` - MTTR/MTBF calculations
- `/energy-monitoring` - Energy consumption monitoring

### PDC (Production Data Collection)
- `/pdc` - Production data collection endpoints

### PokaYoke
- `/pokayoke` - Quality checklist management

---

## 💾 Database Models

### Core Models

#### User Models (`app/models/user.py`)
- `UserRole` - User roles (admin, operator, etc.)
- `User` - User accounts
- `MachineCredential` - Machine authentication

#### Production Models (`app/models/production.py`)
- `StatusLookup` - Machine status definitions
- `MachineRaw` - Raw machine data (historical)
- `MachineRawLive` - Live machine data
- `ShiftSummary` - Shift-wise production summary
- `ShiftInfo` - Shift timing configuration
- `ConfigInfo` - Machine configuration
- `MachineDowntimes` - Downtime tracking
- `OEEIssue` - OEE issues

#### Order Models (`app/models/master_order.py`)
- `Project` - Production projects
- `Order` - Production orders
- `Operation` - Manufacturing operations
- `Program` - CNC programs
- `Machine` - Machine definitions
- `LeadTime` - Lead time calculations

#### Scheduled Models (`app/models/scheduled.py`)
- `PlannedScheduleItem` - Planned schedule items
- `RescheduleHistory` - Reschedule history
- `ProductionLog` - Production logs

#### Quality Models (`app/models/quality.py`)
- `MasterBoc` - Bill of Components
- Quality inspection models

#### Inventory Models (`app/models/inventoryv1.py`)
- `InventoryCategory` - Material categories
- `InventorySubCategory` - Sub-categories
- `InventoryItem` - Inventory items
- `CalibrationSchedule` - Calibration scheduling
- `CalibrationHistory` - Calibration history
- `InventoryRequest` - Inventory requests
- `InventoryTransaction` - Inventory transactions

#### Document Models
- **v1** (`app/models/document_management.py`):
  - `DocFolder`, `DocType`, `Document`, `DocumentVersion`, `DocumentAccessLog`
- **v2** (`app/models/document_management_v2.py`):
  - `FolderV2`, `DocumentTypeV2`, `DocumentV2`, `DocumentVersionV2`, `DocumentAccessLogV2`

#### Logs Models (`app/models/logs.py`)
- `MachineStatusLog` - Machine status logging
- `RawMaterialStatusLog` - Raw material status
- `PokaYokeChecklist` - Quality checklists
- `PokaYokeChecklistItem` - Checklist items
- `PokaYokeCompletedLog` - Completed checklists
- `MachineCalibrationLog` - Calibration logs
- `InstrumentCalibrationLog` - Instrument calibration
- `AssetLog` - Asset logging

#### Energy Monitoring Models (`app/models/energymonitoring.py`)
- Energy consumption models
- OPC-UA integration models

---

## 🎯 Key Features

### 1. **Real-Time Production Monitoring**
- Live machine data tracking
- OEE (Overall Equipment Effectiveness) calculation
- Shift-wise production summaries
- Component status monitoring

### 2. **Production Planning & Scheduling**
- Priority-based scheduling
- Dynamic rescheduling
- Planned schedule generation
- Operation sequencing

### 3. **Quality Control**
- PokaYoke checklists
- Quality inspection management
- Master BOC tracking

### 4. **Inventory Management**
- Material tracking
- Calibration scheduling
- Inventory requests and approvals
- Transaction logging

### 5. **Document Management**
- Version control
- File storage (MinIO)
- Access logging
- Folder organization

### 6. **Energy Monitoring**
- OPC-UA data collection
- Energy consumption tracking
- MTTR/MTBF calculations

### 7. **Operator Management**
- Operator login/logout
- Operator activity logging
- Operator assignments

### 8. **Notifications**
- Real-time notifications
- Calibration reminders
- Status change alerts

### 9. **Performance Monitoring**
- Endpoint performance tracking
- CPU time monitoring
- Response time analysis

---

## 🚀 Deployment

### Configuration Files
- `dockerfile` - Docker image definition
- `docker-compose.yml` - Container orchestration
- `requirements.txt` - Python dependencies
- `ENV@bel` - Environment variables

### Database Setup
- PostgreSQL with multiple schemas
- Automatic schema creation on startup
- Migration support

### Services
- **Application Server**: Uvicorn with 4 workers
- **Database**: PostgreSQL
- **File Storage**: MinIO
- **Monitoring**: Performance metrics endpoint

### Environment Variables
```env
# Database
DB_HOST=172.16.0.203
DB_PORT=5432
DB_NAME=BEL_MES4
DB_USER=cmtismc
DB_PASSWORD=cmtismc@2025

# Authentication
SECRET_KEY=BEL_MES_25
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=99999
REFRESH_TOKEN_EXPIRE_DAYS=30

# MinIO
MINIO_ENDPOINT=172.16.0.203:9000
MINIO_ACCESS_KEY=MrKxgiZXGyBArDz8bEnl
MINIO_SECRET_KEY=DJnTcMpypd6x75DlQfCM2MocFIjRON0jU06OgKnn
MINIO_BUCKET_NAME=documents
MINIO_SECURE=false
```

---

## 📝 Notes

### Unused/Copy Files (Should be removed)
The following files are duplicates or copies and should be cleaned up:
- `app/models/inventoryv1 copy.py`
- `app/models/master_order-copy.py`
- `app/models/production-copy.py`
- `app/api/v1/endpoints/document_management copy.py`
- `app/api/v1/endpoints/dynamic_rescheduling_copy.py`
- `app/api/v1/endpoints/dynamic_rescheduling_final.py`
- `app/api/v1/endpoints/inventoryv1 copy.py`
- `app/api/v1/endpoints/mttr_mtbf_copy.py`
- `app/api/v1/endpoints/production_monitoring_changed.py`
- `app/schemas/document_schemas copy.py`
- `app/schemas/inventoryv1 copy.py`
- `app/schemas/scheduled1.py`
- `app/algorithm/scheduling_copy.py`
- `app/algorithm/schedulingcopy2.py`
- Various backup deployment scripts

### Important Active Files
All files in the main structure above are actively used in the application.

---

## 📚 Additional Documentation
- `README.md` - General project documentation
- `README_POKAYOKE.md` - PokaYoke feature documentation
- `MIGRATION_GUIDE.md` - Database migration guide

---

## 🏷️ Version
- **Current Version**: finalv1
- **Last Updated**: 2025

