# Screening App

## Domain

The **screening** app stores tenant screening data from **BoomPay BoomScreen**. BoomScreen handles credit checks, criminal background checks, eviction history, income verification, landlord references, and identity verification.

This app uses a **minimal schema with JSONField** because detailed BoomScreen API documentation (field-level schema) is not yet available. The models capture the structural relationships (application → reports) and key status fields, while storing full API payloads in `raw_data` and `report_data` JSON fields for future schema refinement.

## Models

### ScreeningApplication
Top-level screening application from BoomScreen.
- `boompay_id` — BoomScreen application ID (unique)
- `application` — **FK** to leasing.Application (links screening to the rental application)
- `unit` — **FK** to properties.Unit
- `applicant_name`, `applicant_email` — Applicant info
- `status` — Pending / In Progress / Completed / Expired
- `submitted_at`, `completed_at`
- `raw_data` — Full BoomScreen API response (JSON)

### ScreeningReport
Individual screening check within an application.
- `screening_application` — **FK** to ScreeningApplication (CASCADE)
- `boompay_id` — BoomScreen report ID (unique)
- `report_type` — Credit Report / Criminal Background / Eviction History / Income Verification / Landlord Reference / Identity Verification
- `decision` — Pass / Fail / Needs Review / Pending
- `completed_at`
- `report_data` — Full report payload (JSON) — credit scores, income details, landlord responses, etc.
- `raw_data` — Full API response

## Key Relationships
- ScreeningApplication → leasing.Application (FK, links to RentVine application)
- ScreeningApplication → properties.Unit (FK)
- ScreeningReport → ScreeningApplication (FK, CASCADE)

## Future Services
- **BoomScreenSyncService** — Pull completed screening results from BoomScreen API
- **BoomScreenWebhookHandler** — Process incoming BoomScreen webhooks for real-time status updates
- **ScreeningDecisionAggregator** — Summarize pass/fail across all report types for a single application
- **SchemaRefinement** — Once full BoomScreen API docs are available, migrate `report_data` JSON fields into proper typed columns (credit_score, income_amount, etc.)
