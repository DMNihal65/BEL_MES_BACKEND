# PokaYoke Checklist System - Implementation Guide

This guide provides comprehensive documentation for integrating the PokaYoke checklist functionality into frontend applications for both operators and supervisors.

## Overview

The PokaYoke checklist system allows for:

1. Creation of dynamic checklists for different machines
2. Assignment of checklists to specific machines
3. Operators to complete checklists before starting operations
4. Supervisors to monitor checklist completion and compliance

## API Endpoints Reference

### For Administrators & Supervisors

#### Creating Checklists

**Endpoint:** `POST /pokayoke/checklists/`  
**Authorization:** Required (User with appropriate permissions) get it from the localstorage for the token  
**Description:** Create a new checklist template with optional items

**Request Format:**
```json
{
  "name": "CNC Machine Startup Checklist",
  "description": "Safety and preparation checks before operating CNC machines",
  "items": [
    {
      "item_text": "Check if safety guards are in place",
      "item_type": "boolean",
      "is_required": true
    },
    {
      "item_text": "Verify oil level (percentage)",
      "item_type": "numerical",
      "is_required": true,
      "expected_value": ">=50"
    },
    {
      "item_text": "Note any unusual sounds or vibrations",
      "item_type": "text",
      "is_required": false
    }
  ]
}
```

**Response Format:**
```json
{
  "id": 1,
  "name": "CNC Machine Startup Checklist",
  "description": "Safety and preparation checks before operating CNC machines",
  "created_at": "2023-07-20T09:30:00",
  "created_by": "12",
  "is_active": true,
  "items": [
    {
      "id": 1,
      "item_text": "Check if safety guards are in place",
      "item_type": "boolean",
      "is_required": true,
      "sequence_number": 1,
      "expected_value": null
    },
    {
      "id": 2,
      "item_text": "Verify oil level (percentage)",
      "item_type": "numerical",
      "is_required": true,
      "sequence_number": 2,
      "expected_value": ">=50"
    },
    {
      "id": 3,
      "item_text": "Note any unusual sounds or vibrations",
      "item_type": "text",
      "is_required": false,
      "sequence_number": 3,
      "expected_value": null
    }
  ]
}
```

#### Listing All Checklists

**Endpoint:** `GET /pokayoke/checklists/`  
**Query Parameters:**
- `active_only` (boolean, default: true) - Show only active checklists

**Response Format:**
```json
[
  {
    "id": 1,
    "name": "CNC Machine Startup Checklist",
    "description": "Safety and preparation checks before operating CNC machines",
    "created_at": "2023-07-20T09:30:00",
    "created_by": "12",
    "is_active": true,
    "items": [...]
  },
  {
    "id": 2,
    "name": "Lathe Machine Checklist",
    "description": "Pre-operation checks for lathe machines",
    "created_at": "2023-07-21T10:15:00",
    "created_by": "8",
    "is_active": true,
    "items": [...]
  }
]
```

#### Get Specific Checklist

**Endpoint:** `GET /pokayoke/checklists/{checklist_id}`  
**Response:** Single checklist object (same format as above)

#### Adding Items to Existing Checklist

**Endpoint:** `POST /pokayoke/checklists/items/`  
**Request Format:**
```json
{
  "checklist_id": 1,
  "item_text": "Check coolant temperature (°C)",
  "item_type": "numerical",
  "is_required": true,
  "expected_value": "18-25"
}
```

**Response Format:**
```json
{
  "message": "Item added successfully",
  "item_id": 4
}
```

#### Assigning Checklists to Machines

**Endpoint:** `POST /pokayoke/assignments/`  
**Request Format:**
```json
{
  "checklist_id": 1,
  "machine_id": 5,
  "machine_make": "HAAS VF-2"
}
```

**Response Format:**
```json
{
  "message": "Checklist assigned to machine successfully",
  "assignment_id": 3
}
```

#### View Checklists Assigned to a Machine

**Endpoint:** `GET /pokayoke/assignments/machine/{machine_id}`  
**Query Parameters:**
- `active_only` (boolean, default: true) - Show only active assignments

**Response Format:**
```json
[
  {
    "id": 3,
    "checklist_id": 1,
    "checklist_name": "CNC Machine Startup Checklist",
    "machine_id": 5,
    "machine_make": "HAAS VF-2",
    "assigned_at": "2023-07-22T08:45:00",
    "assigned_by": "12",
    "is_active": true
  }
]
```

#### View Checklist Completion Logs

**Endpoint:** `GET /pokayoke/logs/`  
**Query Parameters:**
- `machine_id` (optional) - Filter by machine ID
- `production_order` (optional) - Filter by production order number
- `part_number` (optional) - Filter by part number
- `operator_id` (optional) - Filter by operator ID
- `from_date` (optional) - Filter by completion date (start)
- `to_date` (optional) - Filter by completion date (end)
- `page` (default: 1) - Page number for pagination
- `page_size` (default: 20, max: 100) - Number of records per page

**Response Format:**
```json
[
  {
    "id": 15,
    "checklist_id": 1,
    "checklist_name": "CNC Machine Startup Checklist",
    "machine_id": 5,
    "operator_id": "34",
    "production_order": "PO-12345",
    "part_number": "BEL-789",
    "completed_at": "2023-07-25T07:30:00",
    "all_items_passed": true,
    "comments": "No issues found",
    "responses": [...]
  },
  {
    "id": 14,
    "checklist_id": 1,
    "checklist_name": "CNC Machine Startup Checklist",
    "machine_id": 5,
    "operator_id": "28",
    "production_order": "PO-12344",
    "part_number": "BEL-456",
    "completed_at": "2023-07-24T16:45:00",
    "all_items_passed": false,
    "comments": "Oil level below recommended value",
    "responses": [...]
  }
]
```

#### View Detailed Checklist Completion Log

**Endpoint:** `GET /pokayoke/logs/{log_id}`  
**Response Format:**
```json
{
  "id": 15,
  "checklist_id": 1,
  "checklist_name": "CNC Machine Startup Checklist",
  "machine_id": 5,
  "operator_id": "34",
  "production_order": "PO-12345",
  "part_number": "BEL-789",
  "completed_at": "2023-07-25T07:30:00",
  "all_items_passed": true,
  "comments": "No issues found",
  "responses": [
    {
      "id": 45,
      "item_id": 1,
      "item_text": "Check if safety guards are in place",
      "response_value": "true",
      "is_conforming": true,
      "timestamp": "2023-07-25T07:29:30"
    },
    {
      "id": 46,
      "item_id": 2,
      "item_text": "Verify oil level (percentage)",
      "response_value": "75",
      "is_conforming": true,
      "timestamp": "2023-07-25T07:29:45"
    },
    {
      "id": 47,
      "item_id": 3,
      "item_text": "Note any unusual sounds or vibrations",
      "response_value": "None observed",
      "is_conforming": true,
      "timestamp": "2023-07-25T07:30:00"
    }
  ]
}
```

### For Operators

#### Get Checklists for a Machine

**Endpoint:** `GET /pokayoke/assignments/machine/{machine_id}`  
**Description:** Retrieve all checklists assigned to a specific machine (same format as described above)

#### Submit Completed Checklist

**Endpoint:** `POST /pokayoke/complete/`  
**Request Format:**
```json
{
  "checklist_id": 1,
  "machine_id": 5,
  "production_order": "PO-12345",
  "part_number": "BEL-789",
  "comments": "No issues found",
  "item_responses": [
    {
      "item_id": 1,
      "item_text": "Check if safety guards are in place",
      "response_value": "true",
      "is_conforming": true
    },
    {
      "item_id": 2,
      "item_text": "Verify oil level (percentage)",
      "response_value": "75",
      "is_conforming": true
    },
    {
      "item_id": 3,
      "item_text": "Note any unusual sounds or vibrations",
      "response_value": "None observed",
      "is_conforming": true
    }
  ]
}
```

**Response Format:**
```json
{
  "id": 15,
  "checklist_id": 1,
  "checklist_name": "CNC Machine Startup Checklist",
  "machine_id": 5,
  "operator_id": "34",
  "production_order": "PO-12345",
  "part_number": "BEL-789",
  "completed_at": "2023-07-25T07:30:00",
  "all_items_passed": true,
  "comments": "No issues found",
  "responses": [...]
}
```

## Frontend Integration Guide

### Supervisor Dashboard Integration

The supervisor dashboard should include the following components:

1. **Checklist Management Interface**:
   - Create new checklists with customizable items
   - View, edit, and manage existing checklists
   - Enable/disable checklists

2. **Machine Assignment Interface**:
   - Assign checklists to specific machines
   - View which checklists are assigned to each machine
   - Update or remove assignments

3. **Compliance Monitoring**:
   - View logs of completed checklists with filtering options
   - Monitor compliance trends and identify recurring issues
   - Generate reports based on completion data

#### Implementation Recommendations

1. **Checklist Creation Workflow**:
   - Use a multi-step form for creating checklists
   - Allow dynamic addition of items with different types (boolean, numerical, text)
   - Provide a preview of the checklist before submission

2. **Assignment Management**:
   - Display a machine list with assigned checklists
   - Allow bulk assignment of checklists to multiple machines
   - Show assignment history and changes

3. **Compliance Dashboard**:
   - Present visual charts showing compliance rates by machine, operator, or time period
   - Highlight non-conforming responses in red
   - Implement date range filtering for logs

### Operator Interface Integration

The operator interface should include:

1. **Machine Selection Screen**:
   - List of machines the operator is authorized to use
   - Status indicators showing whether checklists are completed

2. **Checklist Completion Interface**:
   - Display all checklists assigned to the selected machine
   - Present items in a clear, sequential format
   - Support different input types based on item_type (toggles, number inputs, text areas)

3. **Confirmation & Submission**:
   - Show a summary of all responses before submission
   - Highlight any non-conforming responses
   - Allow addition of comments

#### Implementation Recommendations

1. **Operator Workflow**:
   - Integrate checklist completion as a mandatory step before starting production
   - Use a wizard-style interface for multi-item checklists
   - Implement auto-save for partial completion

2. **UX Design**:
   - Use clear visual indicators for conforming/non-conforming responses
   - Implement guided navigation through checklist items
   - Provide immediate feedback on numerical inputs (e.g., color coding based on expected_value)

3. **Offline Support**:
   - Consider implementing offline capability for checklist completion
   - Queue submissions if network connectivity is lost
   - Synchronize once connection is restored

## Data Model Reference

### PokaYokeChecklist

| Field | Type | Description |
|-------|------|-------------|
| id | Int | Primary key |
| name | String | Name of the checklist |
| description | String | Optional description |
| created_at | DateTime | When the checklist was created |
| created_by | String | User ID who created the checklist |
| is_active | Boolean | Whether the checklist is active |

### PokaYokeChecklistItem

| Field | Type | Description |
|-------|------|-------------|
| id | Int | Primary key |
| checklist_id | Int | Reference to parent checklist |
| item_text | String | Text of the checklist item |
| sequence_number | Int | Order within the checklist |
| item_type | String | Type of item (boolean, numerical, text) |
| is_required | Boolean | Whether the item is required |
| expected_value | String | Expected value or range if applicable |

### PokaYokeChecklistMachineAssignment

| Field | Type | Description |
|-------|------|-------------|
| id | Int | Primary key |
| checklist_id | Int | Reference to checklist |
| machine_id | Int | Reference to machine |
| machine_make | String | Machine make/model |
| assigned_at | DateTime | When the assignment was made |
| assigned_by | String | User ID who assigned |
| is_active | Boolean | Whether the assignment is active |

### PokaYokeCompletedLog

| Field | Type | Description |
|-------|------|-------------|
| id | Int | Primary key |
| checklist_id | Int | Reference to checklist |
| machine_id | Int | Reference to machine |
| operator_id | String | User ID who completed the checklist |
| production_order | String | Production order number |
| part_number | String | Part number being produced |
| completed_at | DateTime | When the checklist was completed |
| all_items_passed | Boolean | Whether all items passed |
| comments | String | Additional comments |

### PokaYokeItemResponse

| Field | Type | Description |
|-------|------|-------------|
| id | Int | Primary key |
| completed_log_id | Int | Reference to completed log |
| item_id | Int | Reference to checklist item |
| item_text | String | Text of the item (for historical record) |
| response_value | String | Value provided by operator |
| is_conforming | Boolean | Whether response meets requirements |
| timestamp | DateTime | When the response was recorded |

## Best Practices

1. **Checklist Design**:
   - Keep checklists concise and focused
   - Use clear, unambiguous language for item text
   - Group related items together
   - Consider hierarchical checklists for complex procedures

2. **Data Validation**:
   - Validate numerical inputs against expected ranges
   - Provide clear feedback for non-conforming responses
   - Allow override with supervisor approval when necessary

3. **Performance Considerations**:
   - Implement pagination for log retrieval
   - Cache active checklists for faster loading
   - Optimize database queries for large datasets

4. **Security**:
   - Enforce proper authorization for checklist creation and assignment
   - Maintain audit trail of changes to checklists
   - Prevent tampering with completed checklist data

5. **Mobile Support**:
   - Design operator interface to work well on tablets and mobile devices
   - Optimize for touch input
   - Consider camera integration for evidence collection

## Troubleshooting

### Common Issues

1. **Missing Items in Response**:
   - Ensure all required items have responses
   - Check that item_id values match the original checklist

2. **Validation Errors**:
   - Verify expected_value format (e.g., ">=50", "18-25")
   - Ensure numerical responses are properly formatted

3. **Authorization Issues**:
   - Verify user has appropriate permissions
   - Check token expiration and refresh if necessary

