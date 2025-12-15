# BEL MES BACKEND - API Endpoints Summary

## Complete API Endpoint Reference

### Base URL
```
http://localhost:8002
```

### Documentation
- Swagger UI: `http://localhost:8002/docs`
- ReDoc: `http://localhost:8002/redoc`

---

## Authentication & User Management

### Authentication (`auth.py`)
- `POST /auth/login` - User login with email/password
- `POST /auth/logout` - User logout
- `POST /auth/register` - User registration
- `POST /auth/refresh` - Refresh access token

### Operator Login (`operator_login.py`)
- `POST /operator-login` - Operator authentication
- `POST /operator-logout` - Operator logout

---

## Production Planning & Scheduling

### Planning (`planning.py`)
- `GET /planning` - Get production plans
- `POST /planning` - Create production plan
- `PUT /planning/{id}` - Update plan
- `DELETE /planning/{id}` - Delete plan

### Master Production Planning (`mpp.py`)
- `GET /mpp` - Get master production plans
- `POST /mpp` - Create MPP
- `PUT /mpp/{id}` - Update MPP
- `DELETE /mpp/{id}` - Delete MPP

### Operations (`operations.py`)
- `GET /operations` - Get operations
- `POST /operations` - Create operation
- `PUT /operations/{id}` - Update operation
- `DELETE /operations/{id}` - Delete operation

### Scheduled Jobs (`scheduled.py` & `scheduled2.py`)
- `GET /scheduled` - Get scheduled jobs
- `POST /scheduled` - Schedule new job
- `PUT /scheduled/{id}` - Update schedule
- `DELETE /scheduled/{id}` - Delete schedule
- Extended endpoints in `scheduled2.py`

### Priority Scheduling (`priority_scheduling.py`)
- `POST /priority-scheduling` - Schedule by priority
- `GET /priority-history` - Get priority history

### Dynamic Rescheduling (`dynamic_rescheduling.py`)
- `POST /reschedule` - Reschedule jobs dynamically
- `GET /reschedule-history` - Get reschedule history

---

## Production Monitoring & Logging

### Production Monitoring (`production_monitoring.py`)
- `GET /machine-data` - Get live machine data
- `GET /machine/{id}/details` - Get machine details
- `GET /oee/{machine_id}` - Get OEE data
- `GET /shift-summary` - Get shift summaries

### Production Logs (`production_logs.py`)
- `POST /production-logs` - Create production log
- `GET /production-logs` - Get production logs
- `GET /production-logs/{id}` - Get specific log
- `PUT /production-logs/{id}` - Update log
- `DELETE /production-logs/{id}` - Delete log

### Daily Production (`daily_production.py`)
- `GET /daily-production` - Get daily production data
- `POST /daily-production` - Record daily production
- `GET /daily-production/{date}` - Get specific date

### Operator Logs (`operator_log.py` & `operatorlog2.py`)
- `POST /operator-log` - Create operator log
- `GET /operator-logs` - Get operator logs
- `GET /operator-logs/{id}` - Get specific log
- Extended in `operatorlog2.py`

---

## Component & Quality Management

### Component Status (`component_status.py`)
- `GET /components` - Get all components
- `GET /component/{id}/status` - Get component status
- `POST /component/update-status` - Update status

### Component Maintenance (`comp_maintainance.py`)
- `GET /maintenance` - Get maintenance records
- `POST /maintenance` - Create maintenance record
- `PUT /maintenance/{id}` - Update maintenance
- `DELETE /maintenance/{id}` - Delete record

### Component Operators (`comp_operator.py`)
- `GET /comp-operators` - Get component operators
- `POST /comp-operator/assign` - Assign operator
- `DELETE /comp-operator/remove` - Remove operator

### Quality Control (`quality.py`)
- `GET /quality/inspections` - Get inspections
- `POST /quality/inspection` - Create inspection
- `GET /quality/defects` - Get defects
- `POST /quality/defect` - Report defect
- `GET /master-boc` - Get BOC data

---

## Programs & Tools

### Programs (`programs.py`)
- `GET /programs` - Get CNC programs
- `POST /programs` - Upload program
- `GET /programs/{id}` - Get specific program
- `DELETE /programs/{id}` - Delete program

### Tool Programs (`toolsprograms.py`)
- `GET /tools-programs` - Get tool programs
- `POST /tools-programs` - Create tool program
- `PUT /tools-programs/{id}` - Update program
- `DELETE /tools-programs/{id}` - Delete program

---

## PDC & Data Collection

### PDC (`pdc.py`)
- `GET /pdc/data` - Get production data
- `POST /pdc/collect` - Collect data
- `GET /pdc/history` - Get data history

---

## Inventory Management

### Inventory (`inventoryv1.py`)
- `GET /inventory` - Get inventory items
- `POST /inventory` - Add item
- `GET /inventory/categories` - Get categories
- `POST /inventory/request` - Request material
- `POST /inventory/approve` - Approve request
- `GET /inventory/calibration-schedules` - Get calibration schedules
- `POST /inventory/calibration` - Schedule calibration

---

## Document Management

### Document Management v1 (`document_management.py`)
- `GET /documents` - Get documents
- `POST /documents/upload` - Upload document
- `GET /documents/{id}` - Get document
- `DELETE /documents/{id}` - Delete document

### Document Management v2 (`document_management_v2.py`)
- `GET /api/v1/document-management/documents` - Get documents
- `POST /api/v1/document-management/upload` - Upload document
- `GET /api/v1/document-management/folders` - Get folders
- `POST /api/v1/document-management/folders` - Create folder
- `GET /api/v1/document-management/versions/{doc_id}` - Get versions
- `POST /api/v1/document-management/download/{doc_id}` - Download document

---

## Notifications

### Notification Service (`notification_service.py`)
- `GET /notifications` - Get notifications
- `POST /notifications/mark-read` - Mark as read
- `POST /notifications/acknowledge` - Acknowledge notification

### Simple Notifications (`simple_notifications.py`)
- `GET /simple-notifications` - Get simple notifications
- `POST /simple-notifications/create` - Create notification

---

## Logging & Monitoring

### New Logs (`newlogs.py`)
- `GET /logs` - Get logs
- `POST /logs` - Create log
- `GET /logs/{id}` - Get specific log

### MTTR/MTBF (`mttr_mtbf.py`)
- `GET /mttr-mtbf` - Get MTTR/MTBF data
- `POST /mttr-mtbf/calculate` - Calculate metrics
- `GET /mttr-mtbf/{machine_id}` - Get machine metrics

### Energy Monitoring (`energymonitoring.py`)
- `GET /energy/monitoring` - Get energy data
- `GET /energy/consumption` - Get consumption data
- `POST /energy/log` - Log energy data

---

## PokaYoke (Quality Checklists)

### PokaYoke (`pokayoke.py` via routes)
- `GET /pokayoke/checklists` - Get checklists
- `POST /pokayoke/checklist` - Create checklist
- `POST /pokayoke/complete` - Complete checklist
- `GET /pokayoke/history` - Get completion history

---

## Master Orders

### Master Orders (`master_order_routes.py` via routes)
- `GET /orders` - Get orders
- `POST /orders` - Create order
- `GET /orders/{id}` - Get specific order
- `PUT /orders/{id}` - Update order
- `DELETE /orders/{id}` - Delete order

---

## HR & Finance

### HR Routes (`hr_routes.py` via routes)
- `GET /employees` - Get employees
- `POST /employees` - Add employee
- `GET /employees/{id}` - Get employee

### Finance Routes (`finance_routes.py` via routes)
- `GET /finance/records` - Get finance records
- `POST /finance/record` - Create finance record

---

## System

### Performance (`main.py`)
- `GET /performance` - Get performance metrics
  - Returns endpoint statistics
  - Average response time
  - CPU time
  - Request counts

### Health (`main.py`)
- `GET /` - Health check
  - Returns: `{"message": "BEL MES API"}`

---

## Authentication

Most endpoints (except `/auth/login` and `/auth/register`) require:
- **Header**: `Authorization: Bearer {access_token}`
- **Token Source**: `/auth/login` endpoint

---

## Common Response Formats

### Success Response
```json
{
  "status": "success",
  "data": { ... }
}
```

### Error Response
```json
{
  "detail": "Error message"
}
```

### Pagination (where applicable)
```json
{
  "items": [...],
  "total": 100,
  "page": 1,
  "size": 20
}
```

---

## Rate Limiting & Performance

- Performance monitoring enabled
- Metrics available at `/performance`
- Request/response time tracking
- CPU time monitoring

---

## Notes

- All timestamps are in UTC
- All IDs are integers
- Pagination: Default page size is 20, max 100
- File uploads: Max 50MB (configurable)
- Authentication: JWT tokens expire after configured minutes

