# Leasing Sprint: BoomPay + Scorecard + Dashboard + Owner Emails

## Phase 1: BoomPay Client Rewrite
- [x] Rewrite `integrations/boompay/client.py` with JWT Bearer auth
- [x] Add `_authenticate()`, `_ensure_auth()`, `_get_auth_headers()` methods
- [x] Add `post()` method and `json_body` param to `_request()`
- [x] Handle 401 → re-authenticate once, retry
- [x] Fix `BOOMPAY.BASE_URL` default to `https://api.production.boompay.app`
- [x] Add `BOOMPAY.WEBHOOK_SECRET` to settings

## Phase 2: BoomScreen Webhook Receiver
- [x] Create `integrations/boompay/processors.py` with handler dispatch
- [x] Handle: application_submitted/approved/declined/under_review/canceled/updated/started
- [x] Handle: identity_verification_finished → upsert ScreeningReport
- [x] Add `BoomPayWebhookAuth` class in `integrations/api.py`
- [x] Add `POST /api/webhooks/boompay/` endpoint

## Phase 3: Applicant Scorecard Model + Scoring Engine
- [x] Add `ApplicantScorecard` model with 7 scoring categories
- [x] Add 5 auto-deny boolean flags
- [x] Implement `compute_score()` method with tier recommendations
- [x] Create `screening/services.py` — auto-population from BoomScreen reports
- [x] Create `screening/api.py` — CRUD + auto-populate endpoints
- [x] Mount screening router at `/api/screening/`
- [x] Register ApplicantScorecard in admin
- [x] Create migration `0002_applicantscorecard.py`

## Phase 4: Leasing Pipeline Dashboard + Auto-Refresh
- [x] Add `GET /api/analytics/leasing-pipeline` endpoint
- [x] Add `GET /api/analytics/leasing-pipeline/scorecards` endpoint
- [x] Add `LeasingPipelineSchema`, `ScorecardSummaryRowSchema`
- [x] Add `leasing_pipeline` view + URL + nav item
- [x] Create `leasing_pipeline.html` template
- [x] Create `leasing_pipeline.js` with charts + table
- [x] Add auto-refresh (60s) to daily_pulse.js
- [x] Add auto-refresh (60s) to portfolio_analytics.js
- [x] Add auto-refresh (60s) to revenue_intelligence.js
- [x] Add auto-refresh (60s) to renewal_pipeline.js
- [x] Add auto-refresh (60s) to owner_reports.js (with edit guard)

## Phase 5: SendGrid Email for Owner Reports
- [x] Add `django-anymail[sendgrid]` to pyproject.toml
- [x] Add SendGrid config to settings (conditional EMAIL_BACKEND)
- [x] Replace mock `send_owner_note` with real `EmailMessage.send()`
- [x] Validate owner has email before sending
- [x] Console fallback when no SendGrid key

## Phase 6: Owner Email Workflow (Area 1)
- [x] Add `opened_at`, `sendgrid_message_id`, `properties_included` to OwnerReportNote
- [x] Add `PropertyWeeklyNote` model (per-unit per-week notes)
- [x] Create migration `0003_owner_report_tracking_and_property_notes`
- [x] Build `GET /analytics/owner-report-detail/{owner_id}` — batch queries for per-unit metrics
- [x] Add schemas: WeeklyMetrics, ShowingFeedback, UpcomingShowing, PriceRecommendation, MarketContext, OwnerReportUnit, PortfolioAvg
- [x] Build PropertyWeeklyNote CRUD endpoints
- [x] Build `POST /dashboard/owner-notes/{id}/preview` — renders HTML email in iframe
- [x] Rewrite `POST /dashboard/owner-notes/{id}/send` — HTML email via SendGrid with BCC + open tracking
- [x] Create `dashboard/templates/emails/owner_report.html` — full branded email template
- [x] Rewrite `owner_reports.html` — add email preview modal
- [x] Rewrite `owner_reports.js` — rich unit cards, metrics grid, property notes, email workflow
- [x] Build `generate_weekly_reports` management command (Monday night auto-generation)
- [x] Add cron entry: `0 22 * * 1` for Monday night draft generation

## Phase 6 — Scorecard Detail Panel
- [x] Add clickable rows to scorecards table with detail panel
- [x] Render all 7 scoring categories, auto-deny flags, notes
- [x] Auto-refresh guard for open detail panel

## Phase 7: Webhook-Primary DailyLeasingMetric — Pre-build Verification

### Timezone boundary verification
- [x] Create `verify_tz_boundaries` management command
- [ ] Run command against production, compare UTC vs Eastern totals
- [ ] Whichever sums to 8 matches RentEngine's PDF → adopt that boundary

### Duplicate rentengine_id audit
- [ ] Run duplicate-check snippet in production Django shell
- [ ] If duplicates exist, plan dedup data migration before adding unique constraints

### Duplicate check snippet (paste into `manage.py shell`):
```python
from django.db.models import Count
from leasing.models import LeasingEvent, Showing

# LeasingEvent duplicates
le_dupes = (
    LeasingEvent.objects
    .filter(rentengine_id__isnull=False)
    .values("rentengine_id")
    .annotate(cnt=Count("id"))
    .filter(cnt__gt=1)
    .order_by("-cnt")
)
print(f"LeasingEvent: {le_dupes.count()} duplicate rentengine_ids")
for d in le_dupes[:10]:
    print(f"  rentengine_id={d['rentengine_id']}  count={d['cnt']}")

# Showing duplicates
sh_dupes = (
    Showing.objects
    .filter(rentengine_id__isnull=False)
    .values("rentengine_id")
    .annotate(cnt=Count("id"))
    .filter(cnt__gt=1)
    .order_by("-cnt")
)
print(f"\nShowing: {sh_dupes.count()} duplicate rentengine_ids")
for d in sh_dupes[:10]:
    print(f"  rentengine_id={d['rentengine_id']}  count={d['cnt']}")
```

## Files Modified/Created
| Action | File |
|---|---|
| Modified | `integrations/boompay/client.py` |
| Modified | `vesta_rental_index/settings.py` |
| Created | `integrations/boompay/processors.py` |
| Modified | `integrations/api.py` |
| Modified | `screening/models.py` |
| Created | `screening/services.py` |
| Created | `screening/api.py` |
| Modified | `screening/admin.py` |
| Modified | `analytics/api.py` |
| Modified | `analytics/schemas.py` |
| Modified | `dashboard/views.py` |
| Modified | `dashboard/urls.py` |
| Modified | `dashboard/templates/dashboard/_base.html` |
| Created | `dashboard/templates/dashboard/leasing_pipeline.html` |
| Created | `dashboard/static/dashboard/js/leasing_pipeline.js` |
| Modified | `dashboard/static/dashboard/js/daily_pulse.js` |
| Modified | `dashboard/static/dashboard/js/portfolio_analytics.js` |
| Modified | `dashboard/static/dashboard/js/revenue_intelligence.js` |
| Modified | `dashboard/static/dashboard/js/renewal_pipeline.js` |
| Modified | `dashboard/static/dashboard/js/owner_reports.js` |
| Modified | `dashboard/api.py` |
| Modified | `pyproject.toml` |
| Created | `screening/migrations/0002_applicantscorecard.py` |
| Modified | `dashboard/models.py` |
| Created | `dashboard/migrations/0003_owner_report_tracking_and_property_notes.py` |
| Created | `dashboard/templates/emails/owner_report.html` |
| Modified | `dashboard/templates/dashboard/owner_reports.html` |
| Created | `dashboard/management/commands/generate_weekly_reports.py` |
| Modified | `cron/crontab` |
