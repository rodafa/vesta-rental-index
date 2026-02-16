# Maintenance App

## Domain

The **maintenance** app tracks property maintenance operations from **RentVine** — work orders, inspections, vendors, and vendor trade categories. This data helps correlate maintenance activity with leasing performance (e.g., units with open work orders may take longer to lease).

## Models

### Vendor
Vendor contact from RentVine (contactTypeID=3).
- `rentvine_contact_id` — RentVine contactID (unique)
- `name`, `email`, `phone`, `website_url`
- `is_active`
- Insurance tracking: `liability_insurance_company`, `liability_insurance_policy_number`, `liability_insurance_expires`, `workers_comp_insurance_company`, `workers_comp_insurance_policy_number`, `workers_comp_insurance_expires`
- `raw_data` — Full API response

### VendorTrade
Trade/specialty categories (e.g., Plumbing, HVAC, Electrical).
- `rentvine_id` — RentVine vendorTradeID (unique)
- `name`
- `is_visible_tenant_portal`

### WorkOrderStatus
Status definitions for work orders. Supports custom statuses beyond the 4 primary categories.
- `rentvine_id` — RentVine workOrderStatusID (unique)
- `primary_status` — Pending / Open / Closed / On Hold
- `name` — Custom status name
- `is_system_status` — Whether this is a built-in RentVine status
- `order_index` — Sort order

### WorkOrder
Maintenance work order from RentVine.
- `rentvine_id` — RentVine workOrderID (unique)
- `work_order_number` — Human-readable WO number
- `property` — **FK** to properties.Property (CASCADE)
- `unit` — **FK** to properties.Unit (optional)
- `lease` — **FK** to leasing.Lease (optional, ties to resident who requested)
- `vendor` — **FK** to Vendor (assigned vendor)
- `vendor_trade` — **FK** to VendorTrade (trade category)
- `status` — **FK** to WorkOrderStatus
- `primary_status` — Pending / Open / Closed / On Hold
- `priority` — Low / Medium / High
- `source_type` — Portal / In Person / Email / Text Message / Phone / Recurring
- `description`, `vendor_instructions`, `closing_description`
- `is_owner_approved`, `is_vacant`
- `estimated_amount`
- Date fields: `scheduled_start_date`, `scheduled_end_date`, `actual_start_date`, `actual_end_date`, `date_closed`
- `raw_data` — Full API response

### Inspection
Property inspection from RentVine.
- `rentvine_id` — RentVine inspectionID (unique)
- `property` — **FK** to properties.Property (CASCADE)
- `unit` — **FK** to properties.Unit (CASCADE)
- `lease` — **FK** to leasing.Lease (optional)
- `inspection_type` — Pre-Inspection / Move In / Move Out / Inspection
- `inspection_status` — Pending / In Progress / Pending Maintenance / Completed
- `description`
- `scheduled_date`, `inspection_date`
- `raw_data` — Full API response

## Key Relationships
- WorkOrder → Property, Unit, Lease, Vendor, VendorTrade, WorkOrderStatus (FK)
- Inspection → Property, Unit, Lease (FK)
- Bill (in accounting app) → WorkOrder (FK, links bills to work orders)

## Future Services
- **WorkOrderSyncService** — Daily pull from RentVine `/maintenance/work-orders`
- **InspectionSyncService** — Daily pull from RentVine `/maintenance/inspections`
- **VendorSyncService** — Sync vendor contacts and trade categories
- **MaintenanceImpactAnalyzer** — Correlate open work orders with unit leasing performance (DOM, showings)
