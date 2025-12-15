# BEL MES BACKEND - Visual Folder Structure

## Complete Active Backend Structure

```
BEL_MES_BACKEND/
│
├── 📄 dockerfile                    # Docker image configuration
├── 📄 docker-compose.yml           # Container orchestration
├── 📄 requirements.txt             # Python dependencies
├── 📄 init-db.sql                  # Database init script
├── 📄 ENV@bel                      # Environment variables
├── 📄 data/
│   └── priority_history.json       # Scheduling priority data
│
└── 📁 app/                         # Main application
    │
    ├── 🚀 main.py                  # FastAPI app entry point
    │
    ├── 📁 algorithm/               # Scheduling algorithms
    │   ├── scheduling.py          # ✓ Main scheduling logic
    │   ├── planned_scheduling.py  # ✓ Planned schedule generation
    │   └── BEL_MES_BACKEND.code-workspace  # VS Code config
    │
    ├── 📁 api/
    │   └── 📁 v1/
    │       └── 📁 endpoints/       # API endpoint modules
    │           ├── auth.py              # ✓ Login/logout/register
    │           ├── operator_login.py    # ✓ Operator auth
    │           ├── planning.py          # ✓ Production planning
    │           ├── mpp.py               # ✓ Master production plan
    │           ├── operations.py        # ✓ Operation management
    │           ├── scheduled.py         # ✓ Schedule v1
    │           ├── scheduled2.py        # ✓ Schedule v2
    │           ├── priority_scheduling.py      # ✓ Priority scheduling
    │           ├── dynamic_rescheduling.py    # ✓ Dynamic reschedule
    │           ├── production_logs.py         # ✓ Production logging
    │           ├── production_monitoring.py   # ✓ Real-time monitoring
    │           ├── daily_production.py        # ✓ Daily reports
    │           ├── component_status.py        # ✓ Component tracking
    │           ├── comp_maintainance.py      # ✓ Maintenance
    │           ├── comp_operator.py          # ✓ Operator assign
    │           ├── programs.py              # ✓ CNC programs
    │           ├── toolsprograms.py         # ✓ Tool programs
    │           ├── pdc.py                   # ✓ Production data
    │           ├── operator_log.py          # ✓ Operator logs
    │           ├── operatorlog2.py         # ✓ Extended logs
    │           ├── quality.py              # ✓ Quality control
    │           ├── inventoryv1.py           # ✓ Inventory
    │           ├── document_management.py   # ✓ Docs v1
    │           ├── document_management_v2.py # ✓ Docs v2
    │           ├── notification_service.py  # ✓ Notifications
    │           ├── simple_notifications.py  # ✓ Simple notifications
    │           ├── mttr_mtbf.py            # ✓ MTTR/MTBF
    │           ├── energymonitoring.py     # ✓ Energy tracking
    │           ├── newlogs.py              # ✓ New logging
    │           └── notifications.sqlite  # Notification DB
    │
    ├── 📁 models/                   # Database models (Pony ORM)
    │   ├── __init__.py             # ✓ Model imports
    │   ├── user.py                 # ✓ Users & auth
    │   ├── master_order.py         # ✓ Orders & projects
    │   ├── production.py           # ✓ Production models
    │   ├── scheduled.py            # ✓ Scheduling models
    │   ├── inventory.py            # ✓ Inventory base
    │   ├── inventoryv1.py          # ✓ Inventory v1
    │   ├── quality.py              # ✓ Quality models
    │   ├── document_management.py  # ✓ Documents v1
    │   ├── document_management_v2.py # ✓ Documents v2
    │   ├── logs.py                 # ✓ Logging models
    │   ├── energymonitoring.py     # ✓ Energy models
    │   ├── hr_models.py            # ✓ HR models
    │   └── finance_models.py       # ✓ Finance models
    │
    ├── 📁 schemas/                  # Pydantic validation schemas
    │   ├── user.py                 # ✓ User schemas
    │   ├── master_order_schemas.py  # ✓ Order schemas
    │   ├── quality.py               # ✓ Quality schemas
    │   ├── inventoryv1.py           # ✓ Inventory schemas
    │   ├── document_schemas.py      # ✓ Doc v1 schemas
    │   ├── document_management_v2.py # ✓ Doc v2 schemas
    │   ├── component_status.py      # ✓ Component schemas
    │   ├── component_quantities.py    # ✓ Component qty
    │   ├── comp_maintainance.py     # ✓ Maintenance schemas
    │   ├── daily_production.py      # ✓ Daily schemas
    │   ├── pdc.py                  # ✓ PDC schemas
    │   ├── operations.py            # ✓ Operation schemas
    │   ├── planning.py              # ✓ Planning schemas
    │   ├── scheduled.py             # ✓ Schedule schemas
    │   ├── mpp.py                   # ✓ MPP schemas
    │   ├── mttr_mtbf.py             # ✓ MTTR schemas
    │   ├── leadtime.py              # ✓ Lead time schemas
    │   ├── raw_material.py          # ✓ Material schemas
    │   ├── toolsprograms.py         # ✓ Tools schemas
    │   ├── pokayoke.py              # ✓ Pokayoke schemas
    │   └── energymonitoring.py      # ✓ Energy schemas
    │
    ├── 📁 crud/                     # CRUD operations
    │   ├── user.py                 # ✓ User CRUD
    │   ├── operation.py            # ✓ Operation CRUD
    │   ├── quality.py              # ✓ Quality CRUD
    │   ├── raw_material.py         # ✓ Material CRUD
    │   ├── component_quantities.py # ✓ Component CRUD
    │   ├── leadtime.py             # ✓ Lead time CRUD
    │   └── pdc.py                  # ✓ PDC CRUD
    │
    ├── 📁 routes/                   # Route modules
    │   ├── master_order_routes.py  # ✓ Order routes
    │   ├── finance_routes.py       # ✓ Finance routes
    │   ├── hr_routes.py            # ✓ HR routes
    │   └── pokayoke.py             # ✓ Pokayoke routes
    │
    ├── 📁 services/                 # External services
    │   ├── minio_service.py       # ✓ MinIO storage
    │   ├── oee_collector_opcua.py # ✓ OPC-UA collector
    │   └── oee_collector_ems.py   # ✓ EMS collector
    │
    ├── 📁 utils/                    # Utility functions
    │   └── production_calculations.py # ✓ Production calcs
    │
    ├── 📁 database/                 # Database configuration
    │   ├── connection.py          # ✓ DB connection
    │   ├── init_db.py             # ✓ DB initialization
    │   └── migrations.py           # ✓ Migrations
    │
    ├── 📁 core/                     # Core functionality
    │   └── security.py            # ✓ Security (JWT, hashing)
    │
    ├── 📁 config/                    # Configuration
    │   └── settings.py            # ✓ App settings
    │
    └── 📁 simulator/               # Data simulation (dev/testing)
        ├── main.py                # Simulation main
        ├── test.py                # Simulation tests
        ├── planned_schedule_items.csv
        ├── planned_schedule_items_2.csv
        └── programs.csv
```

## Legend
- ✓ = Active and used in production
- 📄 = File
- 📁 = Directory
- 🚀 = Main entry point

## Files to Remove (Unused/Duplicate)
```
❌ app/models/inventoryv1 copy.py
❌ app/models/master_order-copy.py
❌ app/models/production-copy.py
❌ app/models/logs_backup.py
❌ app/api/v1/endpoints/document_management copy.py
❌ app/api/v1/endpoints/dynamic_rescheduling_copy.py
❌ app/api/v1/endpoints/dynamic_rescheduling_final.py
❌ app/api/v1/endpoints/inventoryv1 copy.py
❌ app/api/v1/endpoints/mttr_mtbf_copy.py
❌ app/api/v1/endpoints/production_monitoring_changed.py
❌ app/schemas/document_schemas copy.py
❌ app/schemas/inventoryv1 copy.py
❌ app/schemas/scheduled1.py
❌ app/algorithm/scheduling_copy.py
❌ app/algorithm/schedulingcopy2.py
❌ Root level backup deployment scripts (* copy.ps1, etc.)
❌ Backend temp files (current_time, applicable_status, etc.)
```

## Root Level Files to Keep
```
✅ dockerfile
✅ docker-compose.yml
✅ requirements.txt
✅ init-db.sql
✅ ENV@bel
✅ data/priority_history.json
✅ README.md
✅ README_POKAYOKE.md
✅ MIGRATION_GUIDE.md
```

