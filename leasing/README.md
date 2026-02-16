# Leasing App

## Domain

The **leasing** app covers the full leasing lifecycle — from the moment a prospect expresses interest in a unit through lease signing and move-in. It bridges two systems:

- **RentVine** provides the contractual side: signed leases, tenant records, and rental applications.
- **RentEngine** provides the pipeline side: prospects (leads), 40+ leasing event types, showings, and conversion tracking.

This is the heart of the Rental Index — the data that feeds conversion ratios (lead-to-show, show-to-app) and leasing performance KPIs.

## Models

### Tenant
Tenant contact from RentVine (contactTypeID=2).
- `rentvine_contact_id` — RentVine contactID (unique)
- `name`, `first_name`, `last_name`, `email`, `phone`
- `is_active`
- `raw_data` — Full API response

### Lease
Signed lease agreement from RentVine.
- `rentvine_id` — RentVine leaseID (unique)
- `unit` — **FK** to properties.Unit (CASCADE)
- `property` — **FK** to properties.Property (CASCADE)
- `tenants` — **M2M** to Tenant
- `primary_lease_status` — Pending / Active / Closed
- `move_out_status` — None / Active / Completed
- Key dates: `move_in_date`, `start_date`, `end_date`, `closed_date`, `notice_date`, `expected_move_out_date`, `move_out_date`, `deposit_refund_due_date`
- Financial: `lease_return_charge_amount`
- Insurance: `renters_insurance_company`, `renters_insurance_policy_number`, `renters_insurance_expiration_date`
- Move-out: `move_out_reason_id`, `move_out_tenant_remarks`
- Forwarding address fields
- `is_renewal` — Whether this lease is a renewal of a previous lease
- `previous_lease` — **FK** to self (SET_NULL) — links to the prior lease in a renewal chain
- `rentvine_application_id` — Links to the application that originated this lease
- `raw_data` — Full API response

### Prospect
Lead from RentEngine. Enters via webhook (INSERT on prospects table).
- `rentengine_id` — RentEngine prospect ID (unique)
- `unit_of_interest` — **FK** to properties.Unit
- `name`, `email`, `phone`, `source`, `status`
- `raw_data` — Full API response

### LeasingEvent
Pipeline event from RentEngine (40+ types). Received via webhooks.
- `rentengine_id` — RentEngine event ID
- `prospect` — **FK** to Prospect
- `unit` — **FK** to properties.Unit
- `event_type` — One of 40+ choices (New, Showing Scheduled, Showing Complete, Missed Showing, Application Received, Lease Signed, Moved In, etc.)
- `event_timestamp`, `event_date` — When the event occurred
- `context` — Additional event payload (JSON)
- `raw_data` — Full webhook payload

### Showing
Showing record from RentEngine.
- `rentengine_id` — RentEngine showing ID
- `prospect` — **FK** to Prospect
- `unit` — **FK** to properties.Unit
- `showing_method` — Accompanied / Self Guided / Remote Guided / Remote Guided with Gated Access
- `status` — Scheduled / Confirmed / Arrived / Started / Completed / Missed / Failed / Canceled
- `scheduled_at`, `completed_at`
- `feedback` — Showing feedback from completed showings (JSON)
- `raw_data` — Full API response

### Application
Rental application from RentVine.
- `rentvine_id` — RentVine applicationID (unique)
- `unit` — **FK** to properties.Unit
- `primary_status` — Pending / Submitted / Screening / Processing / On Hold / Approved / Declined / Withdrawn
- `number` — Application number
- Address fields at time of application
- `raw_data` — Full API response

### Applicant
Individual person on a RentVine application.
- `rentvine_id` — RentVine applicantID (unique)
- `application` — **FK** to Application (CASCADE)
- `name`, `email`, `phone`

## Key Relationships
- Lease → Unit, Property (FK)
- Lease ↔ Tenant (M2M)
- Prospect → Unit of Interest (FK)
- LeasingEvent → Prospect, Unit (FK)
- Showing → Prospect, Unit (FK)
- Application → Unit (FK)
- Applicant → Application (FK, CASCADE)
- Lease → Lease (self-FK for renewal chains)

## Future Services
- **WebhookHandlerService** — Process incoming RentEngine webhooks into LeasingEvent and Prospect records
- **LeaseSyncService** — Daily pull from RentVine `/leases/export` to sync lease data
- **ApplicationSyncService** — Daily pull from RentVine `/screening/applications/export`
- **RenewalTracker** — Track renewal rates by property, zip code, and price band from the `is_renewal` / `previous_lease` chain
- **ConversionTracker** — Calculate lead-to-show and show-to-app ratios from LeasingEvent data
- **SlackNotifier** — Send daily/weekly leasing summaries to Slack (replaces Apps Script `sendSlackMessage`)
