# BEL MES BACKEND - Quick Reference Guide

## 🚀 Getting Started

### Run the Backend
```bash
# Using Docker
docker-compose up

# Or directly with Uvicorn
uvicorn app.main:app --host 0.0.0.0 --port 8002 --workers 4
```

### Access Documentation
- Swagger UI: http://localhost:8002/docs
- ReDoc: http://localhost:8002/redoc
- Health Check: http://localhost:8002/

---

## 📋 Quick Links

| Document | Description |
|----------|-------------|
| [BACKEND_STRUCTURE_DOCUMENTATION.md](BACKEND_STRUCTURE_DOCUMENTATION.md) | Complete project structure |
| [BACKEND_FOLDER_STRUCTURE.md](BACKEND_FOLDER_STRUCTURE.md) | Visual folder tree |
| [API_ENDPOINTS_SUMMARY.md](API_ENDPOINTS_SUMMARY.md) | All API endpoints |
| [DATABASE_SCHEMA_DOCUMENTATION.md](DATABASE_SCHEMA_DOCUMENTATION.md) | Database structure |

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                      FastAPI App                       │
│                    (app/main.py)                       │
└─────────────────────────────────────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
┌───────▼────────┐  ┌─────▼──────┐  ┌──────▼───────┐
│   API Routes   │  │  Database   │  │   Services   │
│                │  │   Models    │  │              │
│  - auth.py     │  │             │  │  - MinIO    │
│  - production  │  │  - User     │  │  - OPC-UA   │
│  - quality     │  │  - Order    │  │  - EMS      │
│  - inventory   │  │  - Product  │  │             │
│  - docs        │  │  - Schedule │  │             │
└────────────────┘  └─────────────┘  └─────────────┘
```

---

## 📂 Core Directories

| Directory | Purpose | Key Files |
|-----------|---------|-----------|
| `app/` | Main application | `main.py` |
| `app/api/v1/endpoints/` | API endpoints | `auth.py`, `production_logs.py`, etc. |
| `app/models/` | Database models | `user.py`, `production.py`, etc. |
| `app/schemas/` | Pydantic schemas | Validation schemas |
| `app/crud/` | CRUD operations | Database operations |
| `app/routes/` | Route modules | Route definitions |
| `app/services/` | External services | MinIO, OPC-UA |
| `app/database/` | DB config | `connection.py` |
| `app/core/` | Core functionality | `security.py` |

---

## 🔐 Authentication

### Login
```bash
POST /auth/login
{
  "email": "user@example.com",
  "password": "password"
}

Response: {
  "access_token": "eyJ...",
  "token_type": "bearer"
}
```

### Use Token
```bash
Authorization: Bearer {access_token}
```

---

## 🎯 Key Endpoints by Feature

### 🏭 Production
| Endpoint | Method | Purpose |
|---------|--------|---------|
| `/production-monitoring` | GET | Live production data |
| `/production-logs` | POST/GET | Production logging |
| `/daily-production` | GET | Daily reports |
| `/mttr-mtbf/{id}` | GET | Machine reliability metrics |

### 📋 Planning & Scheduling
| Endpoint | Method | Purpose |
|---------|--------|---------|
| `/planning` | GET/POST | Production planning |
| `/mpp` | GET/POST | Master Production Plan |
| `/scheduled` | GET | Get schedules |
| `/priority-scheduling` | POST | Priority scheduling |
| `/reschedule` | POST | Dynamic rescheduling |

### ✅ Quality Control
| Endpoint | Method | Purpose |
|---------|--------|---------|
| `/quality/inspections` | GET | Quality inspections |
| `/quality/defect` | POST | Report defects |
| `/pokayoke` | POST/GET | Quality checklists |

### 📦 Inventory
| Endpoint | Method | Purpose |
|---------|--------|---------|
| `/inventory` | GET | Get inventory |
| `/inventory/request` | POST | Request material |
| `/inventory/calibration-schedules` | GET | Calibration schedules |

### 📄 Documents
| Endpoint | Method | Purpose |
|---------|--------|---------|
| `/api/v1/document-management/folders` | GET | Get folders |
| `/api/v1/document-management/upload` | POST | Upload document |
| `/api/v1/document-management/download/{id}` | POST | Download |

### 👤 User Management
| Endpoint | Method | Purpose |
|---------|--------|---------|
| `/auth/login` | POST | Login |
| `/auth/register` | POST | Register |
| `/operator-login` | POST | Operator login |
| `/operator-log` | POST | Operator logs |

---

## 🗄️ Database Schemas

| Schema | Purpose | Key Tables |
|--------|---------|------------|
| `auth` | Authentication | users, user_roles |
| `master_order` | Orders | orders, operations, projects |
| `production` | Production | machine_raw, machine_raw_live |
| `scheduling` | Scheduling | planned_schedule_items |
| `quality` | Quality | master_boc |
| `inventoryv1` | Inventory | inventory_items |
| `document_management_v2` | Documents | documentv2, folderv2 |
| `logs` | Logging | pokayoke, calibration_logs |
| `EMS` | Energy | Energy monitoring |

---

## 🔧 Technologies

| Technology | Version | Purpose |
|------------|---------|---------|
| Python | 3.10 | Language |
| FastAPI | 0.115.6 | Web framework |
| Pony ORM | 0.7.19 | ORM |
| PostgreSQL | - | Database |
| MinIO | 7.2.5 | File storage |
| Uvicorn | 0.34.0 | ASGI server |
| JWT | python-jose | Authentication |

---

## 📊 Key Models

### Production Models
- `MachineRawLive` - Current machine status
- `ShiftSummary` - Production summaries
- `MachineDowntimes` - Downtime tracking

### Order Models
- `Order` - Production orders
- `Operation` - Manufacturing operations
- `Program` - CNC programs

### Quality Models
- `MasterBoc` - Bill of Components
- `PokaYokeChecklist` - Quality checklists

### Inventory Models
- `InventoryItem` - Inventory items
- `CalibrationSchedule` - Calibration schedules

---

## 🛠️ Common Tasks

### Add New Endpoint
1. Create file in `app/api/v1/endpoints/`
2. Import in `app/main.py`
3. Add router: `app.include_router(your_module.router)`

### Add New Model
1. Create in `app/models/`
2. Import in `app/models/__init__.py`
3. Import in `app/database/connection.py`

### Add New Schema
1. Create in `app/schemas/`
2. Use in endpoints for validation

---

## 🔍 Troubleshooting

### Database Connection Issues
- Check `.env` file settings
- Verify PostgreSQL is running
- Check schema creation logs

### Import Errors
- Ensure all models imported in `__init__.py`
- Check for circular imports
- Verify all dependencies in `requirements.txt`

### Performance Issues
- Check `/performance` endpoint
- Review worker count (default: 4)
- Check database query performance

---

## 📝 File Status

### ✅ Active Files (Used)
All files listed in `BACKEND_STRUCTURE_DOCUMENTATION.md` are actively used.

### ❌ Files to Remove
- Any file with "copy" in the name
- Backup files (*.bak, *.old)
- Temporary files
- Duplicate endpoint files

---

## 🚨 Important Notes

1. **Authentication**: Most endpoints require JWT token
2. **Database**: Auto-creates schemas on startup
3. **File Storage**: MinIO for document storage
4. **Real-time**: Machine data updates via OPC-UA
5. **Notifications**: Automatic for calibration logs

---

## 📞 Support

- Review API docs at `/docs`
- Check logs for errors
- Database: `BEL_MES4` on `172.16.0.203`
- MinIO: `172.16.0.203:9000`

---

## 🎓 Learning Path

1. **Start here**: `app/main.py` - Application entry
2. **Understand auth**: `app/core/security.py`
3. **Explore endpoints**: `app/api/v1/endpoints/`
4. **Study models**: `app/models/`
5. **Review database**: `DATABASE_SCHEMA_DOCUMENTATION.md`

---

## 📅 Version Information

- **Current Version**: finalv1
- **Branch**: finalv1
- **Last Updated**: 2025
- **Status**: Production Ready

