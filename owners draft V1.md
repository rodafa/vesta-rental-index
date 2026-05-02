# VESTA RENTAL INDEX — COMPREHENSIVE PROJECT STATUS SUMMARY

**As of 2026-05-02**

---

## 1. INSTALLED APPS

All apps registered in `INSTALLED_APPS` (settings.py):

### Django Built-ins
- **django.contrib.admin** — Django admin interface
- **django.contrib.auth** — User authentication and permissions
- **django.contrib.contenttypes** — Content type framework
- **django.contrib.sessions** — Session management
- **django.contrib.messages** — User messaging framework
- **django.contrib.staticfiles** — Static file management

### Third-party
- **ninja** — Fast REST API framework (replaces DRF)
- **anymail** — Email backend (conditional on SENDGRID_API_KEY)

### Vesta Custom Apps

#### **properties** (`properties/`)
- Models: Portfolio, Owner, Property, Unit, MultifamilyProperty, Floorplan
- Purpose: Core domain models for properties under management and syndication
- Manages: Portfolios and owners from RentVine, units with market/leasing data
- Exposes: `/api/properties/` CRUD endpoints (list, detail, filters)

#### **leasing** (`leasing/`)
- Models: Tenant, Lease, Prospect, LeasingEvent, Showing, Application, Applicant, KnownListing
- Purpose: Leasing pipeline from RentEngine prospects through signed leases
- Manages: Lead sources, showings, applications, leasing events (40+ event types)
- Exposes: `/api/leasing/` CRUD, `/api/listing-alerts/` for email deduplication

#### **market** (`market/`)
- Models: DailyUnitSnapshot, DailyMarketStats, DailyLeasingSummary, WeeklyLeasingSummary, MonthlyMarketReport, DailySegmentStats, PriceDrop, ListingCycle, MonthlySegmentStats
- Purpose: Time-series market data aggregation and analysis
- Manages: Daily snapshots (price, DOM, status), aggregated stats, price drop detection
- Exposes: `/api/market/` CRUD endpoints

#### **screening** (`screening/`)
- Models: ScreeningApplication, ScreeningReport, ApplicantScorecard
- Purpose: Applicant screening workflows via BoomPay/BoomScreen
- Manages: Tenant screening results, credit/criminal checks, digitized leasing scorecards (100-point system)
- Exposes: `/api/screening/` for scorecard CRUD and auto-population

#### **maintenance** (`maintenance/`)
- Models: Vendor, VendorTrade, WorkOrderStatus, WorkOrder, Inspection, Meld
- Purpose: Maintenance request tracking and vendor management
- Manages: RentVine work orders + Property Meld melds (unified view)
- Features: Vulcan Slack bot (`ai_service.py`), daily AI-drafted summaries
- Exposes: `/api/maintenance/` for daily summary trigger/debug, Slack event handler at `/maintenance/slack/events/`

#### **accounting** (`accounting/`)
- Models: ChartOfAccounts, Ledger, Transaction, TransactionEntry, Bill
- Purpose: Financial ledger from RentVine
- Manages: Account hierarchies, transactions, bills, double-entry bookkeeping

#### **integrations** (`integrations/`)
- Models: WebhookEvent, APISyncLog, PipelineRun
- Purpose: External API orchestration and webhook handling
- Subdirs: `rentvine/`, `rentengine/`, `boompay/`, `property_meld/`, `leadsimple/` (placeholder)
- Commands: `sync_rentvine_*`, `sync_rentengine_*`, `sync_boompay_*`, `sync_property_meld_melds`, `run_pipeline`
- Exposes: `/api/webhooks/` for RentEngine/BoomPay events, `/api/pipeline/` for background sync

#### **dashboard** (`dashboard/`)
- Models: StaffProfile, OwnerReportNote, PropertyWeeklyNote, MeldWeeklyDraft, UnitNote
- Purpose: Management dashboards and reporting UI
- Features: Daily pulse, portfolio analytics, renewal pipeline, leasing pipeline, owner report drafting, maintenance email drafting
- Middleware: `LoginRateLimitMiddleware`, `ForcePasswordChangeMiddleware`

#### **reports** (`reports/`)
- Models: OwnerReportLog
- Purpose: Monthly AI-generated owner notes (new 2026-04)
- Services: `monthly_owner_report.py`, data sources (RentVine, Property Meld, LeadSimple)
- Command: `generate_monthly_owner_notes`
- Exposes: `/api/reports/owner-notes/`

#### **onboarding** (`onboarding/`)
- Models: OwnerOnboard
- Purpose: Capture property details during owner onboarding (form submission)
- Exposes: `/api/onboard/` for submissions

#### **analytics** (`analytics/`)
- Purpose: High-level portfolio analytics and insights
- Exposes: `/api/analytics/` with ~20 analytical endpoints

---

## 2. MODELS — COMPLETE SCHEMA

### **properties.Portfolio**
```
rentvine_id: IntegerField, unique, indexed
name: CharField(255)
is_active: BooleanField, default=True
reserve_amount: DecimalField(12,2), default=0
additional_reserve_amount: DecimalField(12,2), default=0
additional_reserve_description: TextField
fiscal_year_end_month: IntegerField, null
hold_distributions: BooleanField, default=False
slug: SlugField(255), unique, auto-generated in save()
raw_data: JSONField
source_created_at: DateTimeField, null
created_at: DateTimeField, auto_now_add
updated_at: DateTimeField, auto_now

Methods: __str__(), save() [auto-generates slug]
Relations: owners (M2M Owner), properties (1:M Property)
```

### **properties.Owner**
```
rentvine_contact_id: IntegerField, unique, indexed
name: CharField(255)
first_name: CharField(100)
last_name: CharField(100)
email: EmailField
phone: CharField(50)
is_active: BooleanField, default=True
portfolios: ManyToManyField(Portfolio)
raw_data: JSONField
created_at: DateTimeField, auto_now_add
updated_at: DateTimeField, auto_now

Relations: report_notes (1:M OwnerReportNote), meld_drafts (1:M MeldWeeklyDraft)
```

### **properties.Property**
```
rentvine_id: IntegerField, unique, null, indexed
rentengine_id: IntegerField, unique, null, indexed
portfolio: ForeignKey(Portfolio), null, set_null
name: CharField(255)
property_type: CharField(20), choices (single_family, apartment, condo, townhouse, duplex, multiplex, loft, mobile_home, commercial, garage)
is_multi_unit: BooleanField, default=False
service_type: CharField(20), choices (full_management, leasing_only, maintenance_only), default=full_management

Address:
  street_number, street_name, address_line_1, address_line_2: CharField
  city, state, postal_code: CharField
  country: CharField(2), default="US"
  latitude, longitude: DecimalField(10,7), null

Management:
  management_fee_setting_id: IntegerField, null
  maintenance_limit_amount: DecimalField(12,2), null
  reserve_amount: DecimalField(12,2), default=0
  date_contract_begins, date_contract_ends: DateField, null
  date_insurance_expires, date_warranty_expires: DateField, null

year_built: IntegerField, null
is_active: BooleanField, default=True
raw_data: JSONField
source_created_at: DateTimeField, null
created_at: DateTimeField, auto_now_add
updated_at: DateTimeField, auto_now

Meta: verbose_name_plural = "properties"
Relations: units (1:M Unit), leases (1:M Lease), work_orders (1:M WorkOrder), transactions (1:M Transaction)
```

### **properties.Unit**
```
rentvine_id: IntegerField, unique, null, indexed
rentengine_id: IntegerField, unique, null, indexed
property: ForeignKey(Property), cascade

name: CharField(255)
address_line_1, address_line_2: CharField
city, state, postal_code: CharField
latitude, longitude: DecimalField(10,7), null

bedrooms, full_bathrooms, half_bathrooms: IntegerField, null
square_feet: IntegerField, null
target_rental_rate, deposit: DecimalField(10,2), null

is_active: BooleanField, default=True
multifamily_property: ForeignKey(MultifamilyProperty), null, set_null

raw_data: JSONField
source_created_at: DateTimeField, null
created_at: DateTimeField, auto_now_add
updated_at: DateTimeField, auto_now

Methods: revenue_units() [classmethod] — filter active, exclude non-revenue units
```

### **properties.MultifamilyProperty**
```
rentengine_id: IntegerField, unique, indexed
name: CharField(255)
text_address: CharField(500)
raw_data: JSONField
created_at, updated_at: DateTimeField
```

### **properties.Floorplan**
```
rentengine_id: IntegerField, unique, indexed
multifamily_property: ForeignKey(MultifamilyProperty), cascade
name: CharField(255)
raw_data: JSONField
created_at, updated_at: DateTimeField
```

### **leasing.Tenant**
```
rentvine_contact_id: IntegerField, unique, indexed
name: CharField(255)
first_name, last_name: CharField
email: EmailField
phone: CharField(50)
is_active: BooleanField, default=True
raw_data: JSONField
created_at, updated_at: DateTimeField

Relations: leases (M2M Lease)
```

### **leasing.KnownListing**
```
listing_id: CharField(255), unique
address: CharField(255)
first_seen_at: DateTimeField, auto_now_add
announcement_sent_at, referral_sent_at: DateTimeField, null

Meta: ordering = ["-first_seen_at"]
Purpose: Deduplication for listing alert emails (180-day window)
```

### **leasing.Lease**
```
rentvine_id: IntegerField, unique, indexed
unit: ForeignKey(Unit), cascade
property: ForeignKey(Property), cascade
tenants: ManyToManyField(Tenant)

primary_lease_status: IntegerField, choices (1=Pending, 2=Active, 3=Closed), null
lease_status_id: IntegerField, null
move_out_status: IntegerField, choices (1=None, 2=Active, 3=Completed), null

Dates:
  move_in_date, start_date, end_date, closed_date: DateField, null
  notice_date, expected_move_out_date, move_out_date: DateField, null
  deposit_refund_due_date: DateField, null

Financial:
  rent_amount: DecimalField(10,2), null [Gross monthly rent from recurring charges where account.isRent]
  pet_rent_amount: DecimalField(10,2), null [Subset of rent_amount from Pet Rent charges]
  lease_return_charge_amount: DecimalField(10,2), default=0

Insurance:
  renters_insurance_company: CharField(255)
  renters_insurance_policy_number: CharField(100)
  renters_insurance_expiration_date: DateField, null

Renewal:
  is_renewal: BooleanField, default=False, indexed
  previous_lease: ForeignKey(self), null, set_null

raw_data: JSONField
source_created_at, created_at: DateTimeField
updated_at: DateTimeField
```

### **leasing.Prospect**
```
rentengine_id: IntegerField, unique, indexed
unit_of_interest: ForeignKey(Unit), null, set_null
name: CharField(255)
email: EmailField
phone: CharField(50)
source: CharField(100) [lead source]
status: CharField(100)
raw_data: JSONField
source_created_at, created_at, updated_at: DateTimeField
```

### **leasing.LeasingEvent**
```
rentengine_id: IntegerField, indexed, null
prospect: ForeignKey(Prospect), null, set_null
unit: ForeignKey(Unit), null, set_null
event_type: CharField(50), choices [40+ types], indexed
event_timestamp: DateTimeField, indexed
event_date: DateField, indexed
context: JSONField, default={}
raw_data: JSONField
created_at: DateTimeField, auto_now_add

Meta: ordering = ["-event_timestamp"]
```

### **leasing.Showing**
```
rentengine_id: IntegerField, indexed, null
prospect: ForeignKey(Prospect), null, set_null
unit: ForeignKey(Unit), null, set_null
showing_method: CharField(30), choices (accompanied, self_guided, remote_guided, remote_guided_gated)
status: CharField(20), choices (scheduled, confirmed, arrived, started, completed, missed, failed, canceled)
scheduled_at, completed_at: DateTimeField, null
feedback: JSONField, default={}
raw_data: JSONField
created_at, updated_at: DateTimeField
```

### **leasing.Application**
```
rentvine_id: IntegerField, unique, null, indexed
unit: ForeignKey(Unit), null, set_null
primary_status: IntegerField, choices (1=Pending, 2=Submitted, 3=Screening, 4=Processing, 5=On Hold, 6=Approved, 7=Declined, 8=Withdrawn), null
number: CharField(50)
raw_data: JSONField
source_created_at, source_modified_at: DateTimeField, null
created_at, updated_at: DateTimeField
```

### **leasing.Applicant**
```
rentvine_id: IntegerField, unique, null, indexed
application: ForeignKey(Application), cascade
name: CharField(255)
email: EmailField
phone: CharField(50)
raw_data: JSONField
created_at, updated_at: DateTimeField
```

### **market.DailyUnitSnapshot**
```
unit: ForeignKey(Unit), cascade
snapshot_date: DateField, indexed
listed_price: DecimalField(10,2), null
days_on_market: IntegerField, null
status: CharField(20), choices (active, leased_pending, occupied, make_ready)
bedrooms, square_feet: IntegerField, null
bathrooms: DecimalField(3,1), null
date_listed, date_off_market: DateField, null
created_at: DateTimeField, auto_now_add

Meta:
  unique_together = ["unit", "snapshot_date"]
  ordering = ["-snapshot_date"]
  indexes = [Index(fields=["snapshot_date", "status"])]
```

### **market.DailyMarketStats**
```
snapshot_date: DateField, unique, indexed
active_unit_count: IntegerField, default=0
average_dom, median_dom: IntegerField, default=0
average_price, median_price: DecimalField(10,2), default=0
count_30_plus_dom: IntegerField, default=0
average_portfolio_rent: DecimalField(10,2), default=0 [signed lease amounts for occupied units]
created_at: DateTimeField, auto_now_add

Meta: ordering = ["-snapshot_date"]
```

### **market.DailyLeasingSummary**
```
summary_date: DateField, indexed
unit: ForeignKey(Unit), cascade
leads_count, showings_completed_count, showings_missed_count, applications_count: IntegerField, default=0
property_display_name: CharField(500)
created_at: DateTimeField, auto_now_add

Meta:
  unique_together = ["summary_date", "unit"]
  ordering = ["-summary_date"]

CRITICAL: Stores CUMULATIVE totals — never SUM across days, use Max or source queries
```

### **market.WeeklyLeasingSummary**
```
week_ending: DateField, indexed
unit: ForeignKey(Unit), cascade
leads_count, showings_completed_count, showings_missed_count, applications_count: IntegerField, default=0
lead_to_show_rate, show_to_app_rate: DecimalField(5,2), null
property_display_name: CharField(500)
created_at: DateTimeField, auto_now_add

Meta: unique_together = ["week_ending", "unit"]
```

### **market.MonthlyMarketReport**
```
report_month: DateField, unique, indexed
average_dom, average_30_plus_dom_count: IntegerField
average_price: DecimalField(10,2)
total_leads, total_showings, total_missed_showings, total_applications: IntegerField, default=0
lead_to_show_rate, show_to_app_rate: DecimalField(5,2), null
created_at: DateTimeField, auto_now_add
```

### **market.DailySegmentStats**
```
snapshot_date: DateField, indexed
segment_type: CharField(30), choices (zip_code, bedrooms, property_type, portfolio, price_band), indexed
segment_value: CharField(100), indexed
active_unit_count: IntegerField, default=0
average_dom: IntegerField, default=0
average_price: DecimalField(10,2), default=0
count_30_plus_dom: IntegerField, default=0
leads_count, showings_count, applications_count: IntegerField, default=0
lead_to_show_rate, show_to_app_rate: DecimalField(5,2), null
created_at: DateTimeField, auto_now_add

Meta: unique_together = ["snapshot_date", "segment_type", "segment_value"]
```

### **market.PriceDrop**
```
unit: ForeignKey(Unit), cascade
previous_price, new_price, drop_amount: DecimalField(10,2)
drop_percent: DecimalField(5,2)
detected_date: DateField, indexed
created_at: DateTimeField, auto_now_add

Meta: constraints = [CheckConstraint(new_price < previous_price)]
```

### **market.ListingCycle**
```
unit: ForeignKey(Unit), cascade
listed_date: DateField, indexed
leased_date, lease_start_date: DateField, null
original_list_price, final_list_price, signed_lease_amount: DecimalField(10,2), null
total_dom: IntegerField, null
total_price_drops: IntegerField, default=0
total_drop_amount: DecimalField(10,2), default=0
list_to_lease_ratio: DecimalField(5,4), null [calculated in save()]
created_at: DateTimeField, auto_now_add
updated_at: DateTimeField, auto_now
```

### **market.MonthlySegmentStats**
```
month: DateField, indexed [first day of month]
zip_code: CharField(20), indexed
bedroom_count: IntegerField, indexed
avg_occupied_rent, avg_list_price: DecimalField(10,2), default=0
avg_dom: IntegerField, default=0
avg_lease_length_months: DecimalField(5,1), default=0
leases_written_count, total_leads, total_showings, total_applications: IntegerField, default=0
avg_credit_score: IntegerField, null
avg_applicant_income: DecimalField(12,2), null
occupied_unit_count, vacant_unit_count: IntegerField, default=0
created_at: DateTimeField, auto_now_add

Meta: unique_together = ["month", "zip_code", "bedroom_count"]
```

### **screening.ScreeningApplication**
```
boompay_id: CharField(255), unique, null, indexed
application: ForeignKey(leasing.Application), null, set_null
unit: ForeignKey(Unit), null, set_null
applicant_name: CharField(255)
applicant_email: EmailField
status: CharField(20), choices (pending, in_progress, completed, expired), default=pending
submitted_at, completed_at: DateTimeField, null
raw_data: JSONField
created_at, updated_at: DateTimeField

Relations: reports (1:M ScreeningReport), scorecard (1:1 ApplicantScorecard)
```

### **screening.ScreeningReport**
```
screening_application: ForeignKey(ScreeningApplication), cascade
boompay_id: CharField(255), unique, null, indexed
report_type: CharField(20), choices (credit, criminal, eviction, income, landlord_ref, identity)
decision: CharField(10), choices (pass, fail, review, pending), default=pending
completed_at: DateTimeField, null
report_data, raw_data: JSONField
created_at, updated_at: DateTimeField
```

### **screening.ApplicantScorecard**
```
screening_application: OneToOneField(ScreeningApplication), cascade

Income & Employment (max 30):
  income_ratio: choices (3x_plus, 2.5x_to_3x, 2x_to_2.5x, below_2x)
  income_ratio_numeric: DecimalField(5,2), null
  income_employment_verified, income_savings_verified: BooleanField

Pets & ESA (max 10):
  pet_status: choices (no_pets, low_risk, medium_risk, high_risk)

Credit & Financial (max 25):
  credit_tier: choices (excellent, good, fair, poor, very_poor)
  credit_score_raw: IntegerField, null
  bankruptcy_status: choices (none, discharged_3plus, discharged_recent, active)
  credit_active_chargeoffs: BooleanField
  dti_tier: choices (low, moderate, high)
  dti_numeric: DecimalField(5,2), null

Rental History (max 20):
  rental_positive_ref, rental_would_rent_again, rental_no_complaints: BooleanField
  eviction_history: choices (none, old, recent)
  rental_owes_landlord: BooleanField

Legal History (max 10):
  legal_no_felonies_5yr, legal_nonviolent_felony_over_5yr: BooleanField
  legal_drug_misdemeanor_3yr: BooleanField
  legal_other_misdemeanor_3yr: IntegerField [count]
  legal_settled_small_claims_3yr, legal_open_small_claims: BooleanField
  legal_settled_landlord_tenant, legal_unpaid_landlord_judgment: BooleanField

Application (max 17):
  app_completed, app_docs_verified, app_good_communication, app_on_time_appointment: BooleanField

Co-Signer (max 15):
  cosigner_strength: choices (none, weak, moderate, strong)

Auto-deny Flags:
  auto_deny_false_info, auto_deny_recent_eviction, auto_deny_violent_felony: BooleanField
  auto_deny_pet_not_disclosed, auto_deny_no_cosigner: BooleanField

Output:
  total_score: IntegerField, default=0
  recommendation: choices (platinum, strong, borderline, high_risk, reject, auto_deny)

Audit:
  reviewed_by: CharField(100)
  notes: TextField
  auto_populated: BooleanField, default=False

Methods: compute_score() [NOT auto-called on save — caller must invoke]
```

### **maintenance.Vendor**
```
rentvine_contact_id: IntegerField, unique, indexed
name: CharField(255)
email: EmailField
phone: CharField(50)
website_url: URLField
[insurance fields: liability + workers comp company/policy/expires]
is_active: BooleanField, default=True
raw_data: JSONField
created_at, updated_at: DateTimeField
```

### **maintenance.WorkOrder**
```
rentvine_id: IntegerField, unique, indexed
work_order_number: IntegerField, null
property: ForeignKey(Property), cascade
unit: ForeignKey(Unit), null, set_null
lease: ForeignKey(Lease), null, set_null
vendor: ForeignKey(Vendor), null, set_null
vendor_trade: ForeignKey(VendorTrade), null, set_null
status: ForeignKey(WorkOrderStatus), null, set_null
primary_status: IntegerField, choices (1=Pending, 2=Open, 3=Closed, 4=On Hold), null
priority: IntegerField, choices (1=Low, 2=Medium, 3=High), null
source_type: IntegerField, choices (1=Portal, 2=In Person, 3=Email, 4=Text, 5=Phone, 6=Recurring), null
description, vendor_instructions, closing_description: TextField
is_owner_approved, is_vacant: BooleanField, default=False
estimated_amount: DecimalField(12,2), null
[scheduled and actual date fields]
raw_data: JSONField
source_created_at, source_modified_at: DateTimeField, null
created_at, updated_at: DateTimeField
```

### **maintenance.Meld**
```
property_meld_id: CharField(255), unique, indexed
brief_description: TextField
category: CharField(255)
priority: CharField(20), choices (LOW, MEDIUM, HIGH, EMERGENCY), indexed
status: CharField(100), indexed

Parties (text-based, no FK):
  assigned_vendor_name: CharField(255)
  coordinator_name: CharField(255)

Property refs (text, no FK):
  property_address: CharField(500)
  property_meld_property_id: CharField(255)
  unit_ref: CharField(255)

resident_presence_required: BooleanField, default=False
scheduled_date, completed_date: DateField, indexed, null
owner_approval_status: CharField(30), choices (Not Requested, Requested, Approved, Not Approved)
has_invoice: BooleanField, default=False
tags: JSONField, default=list
raw_data: JSONField
source_created_at, source_modified_at: DateTimeField, null, indexed
created_at, updated_at: DateTimeField

Meta: indexes on status+priority, owner_approval_status+source_modified_at, scheduled_date+status, completed_date+has_invoice
```

### **accounting.ChartOfAccounts**
```
rentvine_id: IntegerField, unique, indexed
parent_account: ForeignKey(self), null, set_null [hierarchical]
account_type_id, account_category_id: IntegerField
number: CharField(50)
name: CharField(255)
is_active: BooleanField, default=True
is_rent, is_deposit, is_escrow, is_prepayment, is_management_fee, etc.: BooleanField
raw_data: JSONField
```

### **accounting.Transaction**
```
rentvine_id: IntegerField, unique, indexed
transaction_type: IntegerField, choices [24 types]
primary_ledger: ForeignKey(Ledger), null, set_null
property: ForeignKey(Property), null, set_null
unit: ForeignKey(Unit), null, set_null
portfolio: ForeignKey(Portfolio), null, set_null
amount: DecimalField(12,2)
description: TextField
is_voided: BooleanField, default=False
date_posted: DateField, indexed
raw_data: JSONField
```

### **dashboard.StaffProfile**
```
user: OneToOneField(User), cascade
must_change_password: BooleanField, default=True
```

### **dashboard.OwnerReportNote**
```
owner: ForeignKey(Owner), cascade
status: CharField(10), choices (draft, reviewed, sent, delivered, bounced), default=draft
notes_text, email_body: TextField
email_subject: CharField(255)
report_date: DateField, indexed
sent_at: DateTimeField, null
opened_at, delivered_at: DateTimeField, null
bounce_reason: TextField
sendgrid_message_id: CharField(255)
properties_included: JSONField, default=list
created_at, updated_at: DateTimeField

Meta: unique_together = ["owner", "report_date"]
```

### **dashboard.MeldWeeklyDraft**
```
owner: ForeignKey(Owner), cascade
week_start: DateField, indexed [Most recent Tuesday]
email_subject: CharField(255)
email_body: TextField [AI-generated, user-editable]
status: CharField(10), choices (draft, sent), default=draft
sent_at: DateTimeField, null
sendgrid_message_id: CharField(255)
meld_count: IntegerField, default=0
properties_included: JSONField, default=list
created_at, updated_at: DateTimeField

Meta: unique_together = ["owner", "week_start"]
```

### **dashboard.UnitNote**
```
unit: ForeignKey(Unit), cascade
author: CharField(100)
note_text: TextField
created_at, updated_at: DateTimeField
```

### **integrations.WebhookEvent**
```
source: CharField(20), choices (rentengine, rentvine, boompay, property_meld), indexed
event_type: CharField(50), indexed [INSERT, UPDATE, DELETE]
table_name: CharField(100), indexed
record, old_record: JSONField, default=dict
processed: BooleanField, default=False, indexed
processed_at, received_at: DateTimeField
processing_error: TextField
```

### **integrations.PipelineRun**
```
status: CharField(20), choices (started, running, completed, failed), default=started
include_reports: BooleanField, default=False
output: TextField
started_at: DateTimeField
completed_at: DateTimeField, null
```

### **reports.OwnerReportLog**
```
owner_id: CharField(100)
owner_name: CharField(255)
report_month: DateField
portfolio_name: CharField(255)
status: CharField(20) [success, failed, skipped]
error_message: TextField
report_data: JSONField, null
generated_note: TextField
created_at: DateTimeField, auto_now_add

Meta: indexes = [Index(fields=["owner_id", "report_month"])]
```

---

## 3. URL ROUTES — ALL PATTERNS

### **Auth & Admin**
- `GET/POST /accounts/login/` → LoginView (name: "login")
- `GET/POST /accounts/logout/` → LogoutView (name: "logout")
- `GET/POST /accounts/password-change/` → PasswordChangeView
- `GET /accounts/password-change/done/` → Custom PasswordChangeCompleteView
- `GET /admin/` → Django admin site

### **API Root**
- `GET /api/health` → Health check (no auth)
- `GET /api/docs` → Swagger UI (DEBUG only)

### **API Routers (all under /api/)**
- `/api/properties/` → properties router
- `/api/leasing/` → leasing router
- `/api/market/` → market router
- `/api/analytics/` → analytics router
- `/api/dashboard/` → dashboard API router
- `/api/webhooks/` → integrations webhooks router
- `/api/screening/` → screening router
- `/api/pipeline/` → pipeline router
- `/api/listing-alerts/` → leasing alerts router
- `/api/maintenance/` → maintenance router
- `/api/onboard/` → onboarding router
- `/api/reports/` → reports router

### **Dashboard Views (HTML)**
- `GET /dashboard/` → daily_pulse (name: "daily_pulse")
- `GET /dashboard/property/<unit_id>/` → property_detail
- `GET /dashboard/portfolio/` → portfolio_analytics
- `GET /dashboard/renewals/` → renewal_pipeline
- `GET /dashboard/renewals/month/<month>/` → renewal_month_detail
- `GET /dashboard/owner-reports/` → owner_reports
- `GET /dashboard/leasing/` → leasing_pipeline
- `GET /dashboard/maintenance-emails/` → maintenance_emails
- `GET /dashboard/owner-notes/` → owner_notes
- `GET /dashboard/monthly-notes/` → monthly_notes

### **Slack Webhooks**
- `POST /onboarding/slack/minerva/events/` → minerva_events
- `POST /maintenance/slack/events/` → slack_events (Vulcan)

### **Owner Dashboard**
- `GET /owner/<portfolio_slug>/` → owner_dashboard (public, no auth)

### **Redirect**
- `GET /` → Redirect to `/dashboard/`

### **Properties API (/api/properties/)**
```
GET  /portfolios                          → list_portfolios
GET  /portfolios/{portfolio_id}           → get_portfolio
GET  /owners                              → list_owners
GET  /owners/{owner_id}                   → get_owner
GET  /properties                          → list_properties
GET  /properties/{property_id}            → get_property
GET  /properties/{property_id}/units      → list_property_units
GET  /units                               → list_units
GET  /units/{unit_id}                     → get_unit
GET  /multifamily                         → list_multifamily
GET  /floorplans                          → list_floorplans
```

### **Analytics API (/api/analytics/)**
```
GET  /portfolio-summary
GET  /leasing-pipeline
GET  /leasing-funnel/{property_id}
GET  /renewal-pipeline
GET  /owner-report-detail/{owner_id}
GET  /rent-gap
GET  /turn-cycle
GET  /vacant-units
GET  /price-drops
GET  /screening-scorecard-summary
GET  /renewal-active
GET  /expiration-cluster
GET  /lease-expiration-detail/{owner_id}
GET  /concession-analysis
GET  /prospect-sources
GET  /revenue-leakage
GET  /active-listings
GET  /owner-active-listings/{owner_id}
GET  /property-performance/{property_id}
```

### **Dashboard API (/api/dashboard/)**
```
GET/PUT /owner-reports, POST /owner-reports/{id}/send
GET/POST /property-notes, PUT /property-notes/{id}
GET/POST /unit-notes, PUT /unit-notes/{id}
GET /meld-drafts, POST /meld-drafts/generate, PUT /meld-drafts/{id}, POST /meld-drafts/{id}/send
GET /analytics/owner/{owner_id}
```

### **Reports API (/api/reports/)**
```
GET  /owner-notes/months       → list distinct report months
GET  /owner-notes/run-status   → check if generation running
GET  /owner-notes              → list notes for month
PUT  /owner-notes/{note_id}    → save edits to generated_note
POST /owner-notes/generate     → start background generation
POST /owner-notes/generate-sync → synchronous (for debugging)
```

### **Screening API (/api/screening/)**
```
GET/POST /screening-applications
GET /scoring/scorecards, POST /scorecards/{id}/auto-populate, POST /scorecards/{id}/compute
```

### **Pipeline API (/api/pipeline/)**
```
POST /trigger     → start background full sync
GET  /status      → latest run status
```

---

## 4. SERVICES & FUNCTIONS

### **RentVine Integration (integrations/rentvine/)**
- Client: HTTP Basic Auth, base URL `https://{subdomain}.rentvine.com/api/manager`
- Pagination: page-based; retry on 429/5xx with exponential backoff
- `list_paginated(path, page_size)` — Iterator over paginated results
- Sync functions: list_portfolios, list_properties, list_units, list_leases, list_tenants, list_vendors, list_work_orders, list_applications, list_chart_of_accounts, list_transactions, list_bills, etc.
- Mappers: map_portfolio, map_owner, map_property, map_unit, map_lease, map_vendor, map_work_order, etc.
- `_safe_datetime(dt_str, format_list)` — Parse datetime, handle microseconds+Z suffix (fixed 2026-03-26)

### **RentEngine Integration (integrations/rentengine/)**
- Client: Bearer JWT, base URL `https://app.rentengine.io/api/public/v1`
- Rate limit: 20 req/5s
- Pagination: limit/offset (0-indexed)
- Sync functions: list_properties, list_floorplans, list_prospects, list_leasing_events, list_showings
- Processors: process_event(source, table_name, event_type, record) — webhook dispatcher

### **BoomPay Integration (integrations/boompay/)**
- Client: OAuth2 client credentials → Bearer JWT
- `_authenticate()` — POST credentials to `/partner/v1/authenticate`
- Sync: list_screening_applications, list_screening_reports
- Processors: webhook dispatcher for screening events

### **Property Meld Integration (integrations/property_meld/)**
- Client: OAuth2 client credentials → Bearer JWT
- Key header: `X-Multitenant-Id: 3140` on every request
- `list_paginated()` — stop signal = no `next` field
- `sync_melds(dry_run=False)` — Full sync of all open melds
- Mappers: map_meld(pm_data) → Django Meld

### **Monthly Owner Report (reports/services/monthly_owner_report.py)**
- `run_monthly_report(month, owner_id, property_id, dry_run)` — Main orchestrator
- `collect_property_data(property_obj, month_start, month_end)` — Per-property data
- `collect_portfolio_data(portfolio, month_start, month_end)` — Portfolio-level financials
- `build_prompt(portfolio, properties_data, portfolio_financials, month_str)` — Builds Claude prompt
- `has_sufficient_data(prop_data)` — Gate to skip if no data
- `_format_meld(meld)` — Format meld for prompt (no vendor name, uses has_vendor bool)
- AI model: claude-haiku-4-5-20251001, max_tokens=800
- System prompt: Bullet format, address-first, no bold/headers/preamble, closes with portal pointer

### **Data Sources (reports/services/data_sources/)**
- `rentvine.py` → get_active_lease, get_financial_summary, get_portfolio_financial_summary, get_work_orders
- `propertymeld.py` → get_melds_for_period(property_obj, month_start, month_end) [excludes MANAGER_CANCELED + TENANT_CANCELED]
- `leadsimple.py` → get_property_pipeline_context (placeholder, not functional)

### **Maintenance AI Service (maintenance/ai_service.py)**
- `handle_mention(user_text, thread_ts, channel)` — Called on @mention in Slack
- `_get_live_snapshot()` — Live Meld DB stats (new today, 24h, open, by status/priority, stale analysis)
- AI model: claude-sonnet-4-5
- Static knowledge: Vesta Maintenance Playbook + Property Meld Help Docs

### **Pipeline Orchestration**
- `trigger_pipeline(include_reports, force)` — Creates PipelineRun, spawns thread
- `_run_pipeline(run_id, include_reports)` — Background executor
- Stale detection: >30 min → mark failed

---

## 5. ENVIRONMENT VARIABLES

### **Django**
- `DJANGO_SECRET_KEY` — Required for production
- `DJANGO_DEBUG` — Boolean (default: "False" in prod)
- `DJANGO_ALLOWED_HOSTS` — Comma-separated allowed hosts
- `DJANGO_CSRF_TRUSTED_ORIGINS` — Comma-separated CSRF origins

### **Database**
- `DATABASE_URL` — Full connection string (Railway auto-provides)
- `POSTGRES_DB/USER/PASSWORD/HOST/PORT` — docker-compose only

### **API Auth**
- `VESTA_API_KEY` — Required for `/api/` endpoints; 43 chars, starts with "vest"

### **RentVine**
- `RENTVINE_SUBDOMAIN`, `RENTVINE_API_KEY`, `RENTVINE_API_SECRET`

### **RentEngine**
- `RENTENGINE_BASE_URL` (default: "https://app.rentengine.io/api/public/v1")
- `RENTENGINE_API_TOKEN`, `RENTENGINE_WEBHOOK_SECRET`

### **BoomPay**
- `BOOMPAY_BASE_URL` (default: "https://api.production.boompay.app")
- `BOOMPAY_API_KEY`, `BOOMPAY_API_SECRET`, `BOOMPAY_WEBHOOK_SECRET`

### **Property Meld**
- `PROPERTY_MELD_BASE_URL`, `PROPERTY_MELD_CLIENT_ID`, `PROPERTY_MELD_CLIENT_SECRET`
- `PROPERTY_MELD_MANAGEMENT_ID` (= "3140"), `PROPERTY_MELD_SUMMARY_CHANNEL`

### **SendGrid**
- `SENDGRID_API_KEY`, `SENDGRID_WEBHOOK_SECRET`
- `VESTA_FROM_EMAIL` (default: "reports@vestapm.com"), `VESTA_LOGO_URL`

### **Slack**
- `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET` (Vulcan)
- `MINERVA_BOT_TOKEN`, `MINERVA_SIGNING_SECRET` (Minerva)

### **Anthropic**
- `ANTHROPIC_API_KEY`

### **LeadSimple** (placeholder)
- `LEADSIMPLE_BASE_URL`, `LEADSIMPLE_API_KEY`, `LEADSIMPLE_PIPELINE_ID`

---

## 6. MANAGEMENT COMMANDS

### **Data Sync**
- `sync_rentvine_all` — Full portfolio sync (portfolios → properties → units → owners → leases)
- `sync_rentvine_portfolios/properties/units/owners/leases` — Individual steps
- `sync_rentengine_all` — Full RentEngine sync (properties, floorplans, prospects, events, showings)
- `sync_rentengine_units` / `sync_rentengine_leasing` — Individual steps
- `sync_boompay_all` — Full BoomPay sync (applications, reports)
- `sync_property_meld_melds [--dry-run]` — Sync melds (cron 7x daily + 2am)
- `sync_showing_feedback` — Sync showing feedback from RentEngine

### **Market**
- `aggregate_market_data [--all]` — Aggregate snapshots → DailyMarketStats + DailySegmentStats, detect PriceDrop, update ListingCycle
- `link_owner_portfolios` — Link Owner records to Portfolio from RentVine relationships

### **Reporting**
- `generate_weekly_reports` — Generate weekly owner reports (Monday night, creates OwnerReportNote drafts)
- `generate_monthly_owner_notes [--dry-run] [--owner-id X] [--property-id PK] [--month YYYY-MM]` — Generate AI monthly notes
- `property_meld_daily_summary [--channel X] [--dry-run]` — AI daily maintenance summary → Slack

### **Pipeline**
- `run_pipeline [--include-reports] [--skip step]` — Master orchestrator (2am nightly):
  1. RentVine sync
  2. RentEngine sync
  3. BoomPay sync
  4. Market aggregation
  5. Owner-portfolio linking
  6. Property Meld sync
  7. Optional: Weekly reports

### **Utilities**
- `seed_data [--clear]` — Populate dev DB with test data
- `create_staff_user --username U --password P --email E`
- `draft_property_notes --owner-id X --date YYYY-MM-DD`
- `import_historical_events`, `import_weekly_sheets`, `import_historical_onboards`, `merge_onboard_sources`
- `check_new_listings` — Check for new RentEngine listings, send email alerts

---

## 7. EXTERNAL INTEGRATIONS

### **RentVine**
- Auth: HTTP Basic Auth (API key + secret)
- Base URL: `https://{subdomain}.rentvine.com/api/manager`
- Endpoints: /portfolios, /properties, /units, /leases, /contacts, /vendortrades, /workorders, /inspections, /applications, /chartofaccounts, /ledgers, /transactions, /transactionentries, /bills
- Models populated: Portfolio, Owner, Property, Unit, Tenant, Vendor, WorkOrder, Inspection, Lease, Application, Applicant, ChartOfAccounts, Ledger, Transaction, Bill
- Sync: On-demand + nightly pipeline (2am)

### **RentEngine**
- Auth: Bearer JWT
- Base URL: `https://app.rentengine.io/api/public/v1`
- Rate limit: 20 req/5s
- Endpoints: /properties, /floorplans, /prospects, /leasing-events, /showings + webhooks
- Models populated: MultifamilyProperty, Floorplan, Prospect, LeasingEvent, Showing, Unit (cross-ref)
- Sync: Webhook-driven; fallback nightly

### **BoomPay / BoomScreen**
- Auth: OAuth2 client credentials → Bearer JWT (POST /partner/v1/authenticate)
- Base URL: `https://api.production.boompay.app`
- Endpoints: /partner/v1/screening-applications, /screening-reports + webhooks
- Models populated: ScreeningApplication, ScreeningReport
- **Known issue: Sync fails in production (credentials not configured)**

### **Property Meld**
- Auth: OAuth2 client credentials (grant_type=client_credentials)
- Base URL: `https://api.propertymeld.com/api/v2`
- Key header: `X-Multitenant-Id: 3140` on every request
- Pagination: limit/offset, stop signal = no `next` field
- Endpoints: /oauth2/token, /melds
- Models populated: Meld (943 records as of 2026-03-25)
- Sync: 7x daily cron + 2am nightly pipeline
- Field mappings:
  - work_category → category
  - tenant_presence_required → resident_presence_required
  - prop_address.full_address → property_address
  - vendor_assignment_requests[].vendor.name → assigned_vendor_name
  - coordinator.user.first/last_name → coordinator_name
  - vendorappointment[].availability_segment.event.dtstart → scheduled_date
  - work_entries non-empty → has_invoice=True

### **LeadSimple** (placeholder, not functional)
- Auth: API key
- Base URL: `https://api.leadsimple.com/v1`
- Purpose: Pipeline context for monthly owner reports
- Status: Awaiting API documentation

### **SendGrid**
- Auth: API key (django-anymail backend)
- Features: Open tracking, bounce handling, BCC support, message ID tracking
- Used by: Owner reports (OwnerReportNote), maintenance emails (MeldWeeklyDraft), password resets

### **Slack — Vulcan (Maintenance AI)**
- Webhook: `POST /maintenance/slack/events/`
- Channel: `#maintenance-gpt`
- Model: claude-sonnet-4-5
- Injects: Live Meld snapshot on every @mention
- Static knowledge: Vesta Maintenance Playbook + Property Meld Help Docs

### **Slack — Property Meld Daily Summary**
- Model: claude-haiku-4-5-20251001
- Destination: `#maintenance-gpt` (consolidated with Vulcan)
- Frequency: Daily cron

### **Anthropic**
- `claude-sonnet-4-5` — Vulcan Slack bot
- `claude-haiku-4-5-20251001` — Monthly owner reports, daily summaries

---

## 8. REPORTS APP — DETAILED STATUS

### What's Implemented
1. **Monthly owner note generation** via AI (Claude Haiku) with data from RentVine + Property Meld
2. **API endpoints**: list, run-status, update, generate (background), generate-sync (debug)
3. **Dashboard integration** at `/dashboard/monthly-notes/` with split-panel UI
4. **OwnerReportLog model** for tracking generation runs (success/failed/skipped)
5. **Dry-run mode** (no DB writes, prints system prompt to stdout)
6. **Background threading** (non-blocking; module-level `_active_runs` + `_last_run_results`)
7. **Selective generation** (--owner-id, --property-id, --month)
8. **Manual note editing** via PUT /owner-notes/{id}

### What's Working
- AI-drafted monthly summaries per portfolio/owner
- RentVine data: active lease, rent, pet_rent, financial summary, work orders
- Property Meld data: melds for period (excluding MANAGER_CANCELED + TENANT_CANCELED)
- Portfolio-level financials: total_received, reserve_amount, additional_reserve_amount, hold_distributions
- Per-lease rent breakdown including pet_rent_amount
- All-time overdue balance
- Dashboard review/edit workflow

### What's Missing / Not Working
1. **LeadSimple integration** — Placeholder only; pipeline context (Applications, Move-Ins, Renewals, Late Rent, Move-Outs, Issues) missing from notes
2. **Email sending from reports** — No auto-send; notes must be manually copied to RentVine or sent via Owner Reports dashboard
3. **Historical note aggregation** — Each month generated independently; no month-over-month context
4. **Multi-unit property notes** — PropertyWeeklyNote model exists but not fully wired into monthly notes
5. **Retroactive meld source dates** — Pre-2026-03-26 melds may have NULL source_created_at; need re-sync

---

## 9. TODOs / KNOWN ISSUES

### Hardcoded Values
- Property Meld MANAGEMENT_ID = 3140 (env var exists but critical)
- Slack channel `#maintenance-gpt` (env var PROPERTY_MELD_SUMMARY_CHANNEL)
- RentEngine base URL (env var but rarely changed)

### Known Bugs / Data Quality
- **DailyLeasingSummary stores CUMULATIVE totals** — Never SUM across days; use Max or source queries
- **Meld source dates may be NULL** for pre-2026-03-26 records (need re-sync after `_safe_datetime` fix)
- **BoomPay sync fails in production** — Credentials not configured; manual scorecard entry as fallback
- `auto_now_add` fields reflect SYNC TIME, not source creation date — always use `source_created_at` for date filtering

### Property Address Matching (brittle)
- Splits on comma: `prop_address.full_address` → `address_line_1__iexact`
- Risk: Mismatches if comma formatting changes

### Missing Functionality
1. **LeadSimple pipeline context** in monthly notes (LEADSIMPLE_API_KEY not configured)
2. **Property Meld webhook handler** not implemented (cron-only sync)
3. **Auto-email from reports app** (manual copy-paste to RentVine or Owner Reports dashboard)
4. **Celery for background jobs** (current: threads, risk of dropped responses)
5. **Incremental RentVine sync** (fetches all records; no delta by updated_at)

### Cascade Delete Risk
- Unit deletion cascades to: DailyUnitSnapshot, DailyLeasingSummary, Prospect, LeasingEvent, Showing, Application, WorkOrder, etc.
- Should soft-delete (is_active=False) instead of hard-delete

### Tech Debt
- Service layer not abstracted (each integration has separate client/mappers/services)
- Minimal test coverage (risk of regressions on sync commands, API changes)
- APISyncLog.error_message is plain TextField (no structure)

### Security Notes
- `VESTA_API_KEY` is only protection for all `/api/` endpoints — rotate regularly
- Slack signing secrets — rotate annually
- No SQL injection risk (Django ORM throughout)
- No XSS risk (Django template escaping throughout)

---

## SUMMARY

The Vesta Rental Index is a comprehensive Django 5.2 rental analytics platform:
- **10 custom apps**: properties, leasing, market, screening, maintenance, accounting, integrations, dashboard, reports, onboarding
- **5 external API integrations**: RentVine (HTTP Basic), RentEngine (Bearer JWT), BoomPay (OAuth2), Property Meld (OAuth2), SendGrid
- **3 AI-powered features**: Vulcan Slack bot (claude-sonnet-4-5), monthly owner notes + daily summaries (claude-haiku-4-5-20251001)
- **Full nightly pipeline**: 2am sync of all data sources + market aggregation
- **Dashboard**: 10 HTML views for staff (pulse, analytics, renewals, leasing, maintenance, reports)

**Critical working flows**: RentVine→properties, RentEngine→leasing webhooks, Property Meld→maintenance melds, Owner Reports email workflow, Monthly Notes generation

**Main gaps**: BoomPay (prod credentials missing), LeadSimple (not implemented), monthly notes email sending, test coverage
