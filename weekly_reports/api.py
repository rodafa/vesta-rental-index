"""
Weekly update API endpoints — ninja Router.

Mounted under /api/reports/weekly-update/ via reports/api.py.
"""

import logging
import threading
from datetime import date
from typing import Optional

from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router, Schema

from weekly_reports.models import WeeklyReportRun

router = Router(tags=["Weekly Update"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class TriggerSchema(Schema):
    start: Optional[str] = None  # YYYY-MM-DD
    end: Optional[str] = None    # YYYY-MM-DD
    dry_run: bool = False
    skip_claude: bool = False
    property_filter: Optional[str] = None


class NoteEditSchema(Schema):
    note_text: str
    approve: bool = True


class ZillowUrlSchema(Schema):
    zillow_url: str


class BlurbSaveSchema(Schema):
    body: str
    start: str  # YYYY-MM-DD — monday anchor computed server-side
    end: str    # YYYY-MM-DD


class SendEmailsSchema(Schema):
    start: str
    end: str
    dry_run: bool = False
    owner_filter: Optional[int] = None


class TestSendSchema(Schema):
    owner_id: int
    start: Optional[str] = None
    end: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_to_dict(run):
    return {
        "id": run.id,
        "start_date": run.start_date.isoformat(),
        "end_date": run.end_date.isoformat(),
        "status": run.status,
        "units_processed": run.units_processed,
        "units_skipped": run.units_skipped,
        "notes_drafted": run.notes_drafted,
        "units_errored": run.units_errored,
        "error_log": run.error_log or [],
        "dry_run": run.dry_run,
        "started_at": run.started_at.isoformat() if run.started_at else "",
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def _note_to_dict(note):
    return {
        "id": note.id,
        "unit_id": note.unit_id,
        "week_date": note.week_date.isoformat(),
        "author": note.author,
        "note_text": note.note_text,
        "approved": note.approved,
        "approved_at": note.approved_at.isoformat() if note.approved_at else None,
        "approved_by": note.approved_by,
        "created_at": note.created_at.isoformat(),
        "updated_at": note.updated_at.isoformat(),
    }


def _get_username(request):
    if hasattr(request, "user") and request.user.is_authenticated:
        return request.user.get_full_name() or request.user.username
    return ""


# ---------------------------------------------------------------------------
# Run endpoints
# ---------------------------------------------------------------------------

@router.post("/trigger")
def trigger_weekly_update(request, payload: TriggerSchema):
    """Trigger a weekly update run in a background thread."""
    from weekly_reports.services.weekly_update import default_date_range, run_for_week

    if payload.start and payload.end:
        try:
            start_date = date.fromisoformat(payload.start)
            end_date = date.fromisoformat(payload.end)
        except ValueError as e:
            return {"ok": False, "error": f"Invalid date format: {e}"}
    else:
        start_date, end_date = default_date_range()

    if end_date < start_date:
        return {"ok": False, "error": "end must be >= start"}
    if end_date > date.today():
        return {"ok": False, "error": "end must be <= today"}
    if (end_date - start_date).days > 31:
        return {"ok": False, "error": "Range must be <= 31 days"}

    running = WeeklyReportRun.objects.filter(
        start_date=start_date, end_date=end_date, status="running"
    ).exists()
    if running:
        return {"ok": False, "error": "A run is already in progress for this period."}

    triggered_by = f"api:{_get_username(request)}" if _get_username(request) else "api"

    run = WeeklyReportRun.objects.create(
        start_date=start_date,
        end_date=end_date,
        dry_run=payload.dry_run,
        triggered_by=triggered_by,
    )
    run_id = run.id

    def _background():
        run_for_week(
            start_date=start_date,
            end_date=end_date,
            dry_run=payload.dry_run,
            skip_claude=payload.skip_claude,
            property_filter=payload.property_filter,
            triggered_by=triggered_by,
            run=run,
        )

    threading.Thread(target=_background, daemon=True).start()
    return {"ok": True, "run_id": run_id, "start": start_date.isoformat(), "end": end_date.isoformat()}


@router.get("/runs/{run_id}")
def get_run_status(request, run_id: int):
    """Poll the status of a weekly update run."""
    try:
        run = WeeklyReportRun.objects.get(id=run_id)
    except WeeklyReportRun.DoesNotExist:
        return {"ok": False, "error": "Run not found"}
    return _run_to_dict(run)


@router.get("/runs")
def list_runs(request):
    """List recent weekly update runs."""
    runs = WeeklyReportRun.objects.order_by("-started_at")[:20]
    return [_run_to_dict(r) for r in runs]


# ---------------------------------------------------------------------------
# Property note endpoints
# ---------------------------------------------------------------------------

@router.post("/property-notes/{note_id}/approve")
def approve_note(request, note_id: int):
    """Approve a PropertyWeeklyNote."""
    from dashboard.models import PropertyWeeklyNote

    note = get_object_or_404(PropertyWeeklyNote, id=note_id)
    username = _get_username(request)

    note.approved = True
    note.approved_at = timezone.now()
    note.approved_by = username
    note.save(update_fields=["approved", "approved_at", "approved_by", "updated_at"])

    logger.info("Note %d approved by %s", note_id, username)
    return _note_to_dict(note)


@router.patch("/property-notes/{note_id}")
def edit_note(request, note_id: int, payload: NoteEditSchema):
    """Edit a PropertyWeeklyNote's text. Optionally approve in the same action."""
    from dashboard.models import PropertyWeeklyNote

    note = get_object_or_404(PropertyWeeklyNote, id=note_id)
    username = _get_username(request)

    note.note_text = payload.note_text
    fields = ["note_text", "updated_at"]

    if payload.approve:
        note.approved = True
        note.approved_at = timezone.now()
        note.approved_by = username
        fields += ["approved", "approved_at", "approved_by"]

    note.save(update_fields=fields)

    logger.info("Note %d edited by %s (approve=%s)", note_id, username, payload.approve)
    return _note_to_dict(note)


@router.post("/property-notes/{note_id}/unapprove")
def unapprove_note(request, note_id: int):
    """Un-approve a PropertyWeeklyNote (mistake recovery)."""
    from dashboard.models import PropertyWeeklyNote

    note = get_object_or_404(PropertyWeeklyNote, id=note_id)
    username = _get_username(request)

    note.approved = False
    note.approved_at = None
    note.approved_by = ""
    note.save(update_fields=["approved", "approved_at", "approved_by", "updated_at"])

    logger.info("Note %d un-approved by %s", note_id, username)
    return _note_to_dict(note)


@router.delete("/property-notes/{note_id}")
def delete_note(request, note_id: int):
    """Hard-delete a PropertyWeeklyNote."""
    from dashboard.models import PropertyWeeklyNote

    note = get_object_or_404(PropertyWeeklyNote, id=note_id)
    username = _get_username(request)

    logger.info(
        "Note %d deleted by %s (unit=%s, week_date=%s)",
        note_id,
        username,
        note.unit_id,
        note.week_date.isoformat(),
    )
    note.delete()

    return {"ok": True, "deleted": note_id}


# ---------------------------------------------------------------------------
# Blurb endpoint
# ---------------------------------------------------------------------------

@router.post("/blurb/save")
def save_blurb(request, payload: BlurbSaveSchema):
    """Save/update the team blurb for the report date range's Monday anchor. Staff-auth only."""
    from datetime import timedelta

    from weekly_reports.services.blurb import upsert_blurb

    if not (hasattr(request, "user") and request.user.is_staff):
        from ninja.errors import HttpError

        raise HttpError(403, "Staff access required")

    try:
        start_date = date.fromisoformat(payload.start)
    except ValueError:
        return {"ok": False, "error": "Invalid start date format (expected YYYY-MM-DD)"}

    monday = start_date - timedelta(days=start_date.weekday())
    blurb = upsert_blurb(monday, payload.body, request.user)
    return {
        "ok": True,
        "last_edited_by": (
            blurb.last_edited_by.get_full_name() or blurb.last_edited_by.username
        ) if blurb.last_edited_by else "",
        "updated_at": blurb.updated_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# View data endpoints (power the dashboard UI)
# ---------------------------------------------------------------------------

@router.get("/properties")
def list_properties_with_notes(request):
    """Return active-listed units with their latest week's note status and metrics.

    Powers the /weekly-reports/ list view.
    """
    from datetime import timedelta

    from dashboard.models import PropertyWeeklyNote
    from properties.models import Unit

    from weekly_reports.services import unit_data
    from weekly_reports.services.weekly_update import default_date_range

    start_date, end_date = default_date_range()

    # Active units with active snapshots
    all_units = list(Unit.objects.filter(is_active=True).select_related("property"))
    snapshots, _ = unit_data.get_snapshot_data([u.id for u in all_units])
    listed_units = [u for u in all_units if snapshots.get(u.id, {}).get("status") == "active"]
    listed_ids = [u.id for u in listed_units]

    # Weekly metrics
    metrics = unit_data.get_weekly_metrics(listed_ids, start_date, end_date)

    # Latest notes for each unit (most recent week_date)
    week_date = start_date - timedelta(days=start_date.weekday())
    notes_map = {}
    for n in PropertyWeeklyNote.objects.filter(unit_id__in=listed_ids).order_by("-week_date"):
        if n.unit_id not in notes_map:
            notes_map[n.unit_id] = n

    result = []
    for u in listed_units:
        snap = snapshots.get(u.id, {})
        wk = metrics.get(u.id, {})
        note = notes_map.get(u.id)
        address = u.address_line_1 or (u.property.address_line_1 if u.property else f"Unit #{u.id}")

        status = "missing"
        if note:
            status = "approved" if note.approved else "draft"

        result.append({
            "unit_id": u.id,
            "address": address,
            "dom": snap.get("days_on_market") or 0,
            "price": float(snap.get("listed_price") or 0),
            "leads": wk.get("leads", 0),
            "showings": wk.get("showings", 0),
            "apps": wk.get("apps", 0),
            "status": status,
            "note_id": note.id if note else None,
            "updated": note.updated_at.isoformat() if note else None,
        })

    return result


@router.get("/properties/{unit_id}")
def get_property_detail(request, unit_id: int):
    """Return unit detail with all weekly notes and stats.

    Powers the /weekly-reports/<unit_id>/ detail view.
    """
    from datetime import timedelta

    from django.db.models import Avg, Sum

    from dashboard.models import PropertyWeeklyNote
    from market.models import DailyLeasingSummary, DailyUnitSnapshot
    from properties.models import Unit

    from weekly_reports.services import unit_data

    unit = get_object_or_404(Unit.objects.select_related("property"), id=unit_id)
    address = unit.address_line_1 or (unit.property.address_line_1 if unit.property else f"Unit #{unit.id}")

    # Current snapshot
    snapshots, _ = unit_data.get_snapshot_data([unit_id])
    snap = snapshots.get(unit_id, {})

    # All notes for this unit
    notes = list(PropertyWeeklyNote.objects.filter(unit_id=unit_id).order_by("-week_date"))

    # All-time cumulative totals from DailyLeasingSummary
    latest_dls = DailyLeasingSummary.objects.filter(unit_id=unit_id).order_by("-summary_date").first()
    alltime_leads = latest_dls.leads_count if latest_dls else 0
    alltime_showings = latest_dls.showings_completed_count if latest_dls else 0
    alltime_apps = latest_dls.applications_count if latest_dls else 0

    # Avg per week over last 8 weeks
    today = date.today()
    eight_weeks_ago = today - timedelta(weeks=8)
    weekly_avgs = {"avg_leads": 0, "avg_showings": 0, "avg_apps": 0}
    if notes:
        recent_notes_count = sum(1 for n in notes if n.week_date >= eight_weeks_ago)
        if recent_notes_count > 0:
            # Use DLS delta for 8-week window
            from weekly_reports.services.weekly_update import default_date_range
            eight_wk_metrics = unit_data.get_weekly_metrics([unit_id], eight_weeks_ago, today)
            wk = eight_wk_metrics.get(unit_id, {})
            # These are cumulative over 8 weeks, divide by weeks
            weeks = max(recent_notes_count, 1)
            weekly_avgs = {
                "avg_leads": round(wk.get("leads", 0) / weeks, 1),
                "avg_showings": round(wk.get("showings", 0) / weeks, 1),
                "avg_apps": round(wk.get("apps", 0) / weeks, 1),
            }

    return {
        "unit_id": unit.id,
        "address": address,
        "bedrooms": unit.bedrooms,
        "bathrooms": (unit.full_bathrooms or 0) + (unit.half_bathrooms or 0) * 0.5,
        "dom": snap.get("days_on_market") or 0,
        "listed_price": float(snap.get("listed_price") or 0),
        "status": snap.get("status", ""),
        "alltime": {
            "leads": alltime_leads,
            "showings": alltime_showings,
            "apps": alltime_apps,
        },
        "weekly_avgs": weekly_avgs,
        "notes": [_note_to_dict(n) for n in notes],
    }


# ---------------------------------------------------------------------------
# Marketing report endpoints
# ---------------------------------------------------------------------------

@router.get("/marketing-report")
def get_marketing_report(request, start: str = None, end: str = None):
    """Return full marketing report data for the week, including blurb."""
    from datetime import timedelta

    from weekly_reports.services.blurb import get_blurb_for_week
    from weekly_reports.services.marketing_report import build_marketing_report
    from weekly_reports.services.weekly_update import default_date_range

    if start and end:
        try:
            week_start = date.fromisoformat(start)
            week_end = date.fromisoformat(end)
        except ValueError as e:
            return {"ok": False, "error": f"Invalid date format: {e}"}
    else:
        week_start, week_end = default_date_range()

    data = build_marketing_report(week_start, week_end)
    data["week_start"] = week_start.isoformat()
    data["week_end"] = week_end.isoformat()

    # Include blurb keyed to the same Monday anchor as property notes
    monday = week_start - timedelta(days=week_start.weekday())
    blurb = get_blurb_for_week(monday)
    data["blurb"] = {
        "body": blurb.body if blurb else "",
        "last_edited_by": (
            blurb.last_edited_by.get_full_name() or blurb.last_edited_by.username
        ) if blurb and blurb.last_edited_by else "",
        "updated_at": blurb.updated_at.isoformat() if blurb else "",
    }

    return data


@router.patch("/properties/{unit_id}/zillow-url")
def update_zillow_url(request, unit_id: int, payload: ZillowUrlSchema):
    """Update a Unit's Zillow URL."""
    from properties.models import Unit

    unit = get_object_or_404(Unit, id=unit_id)
    unit.zillow_url = payload.zillow_url
    unit.save(update_fields=["zillow_url", "updated_at"])

    logger.info("Unit %d zillow_url updated by %s", unit_id, _get_username(request))
    return {"ok": True, "unit_id": unit_id, "zillow_url": unit.zillow_url}


# ---------------------------------------------------------------------------
# Email send endpoints (Phase 3)
# ---------------------------------------------------------------------------

@router.get("/marketing-report/email-preview")
def email_preview(
    request, owner_id: int, start: str = None, end: str = None, format: str = "json",
):
    """Return rendered HTML preview of owner email.

    Pass ``format=html`` to get the raw rendered email for browser viewing.
    """
    from django.http import HttpResponse

    from properties.models import Owner

    from weekly_reports.services.marketing_report import build_marketing_report
    from weekly_reports.services.owner_email_html import build_owner_email_html
    from weekly_reports.services.weekly_update import default_date_range

    owner = get_object_or_404(Owner, id=owner_id)

    if start and end:
        week_start = date.fromisoformat(start)
        week_end = date.fromisoformat(end)
    else:
        week_start, week_end = default_date_range()

    report = build_marketing_report(week_start, week_end)
    # Filter to this owner's units
    owner_rows = [r for r in report["rows"] if owner.name in (r.get("owner_names") or "")]

    html = build_owner_email_html(owner, owner_rows, report["benchmarks"], week_start, week_end)

    if format == "html":
        return HttpResponse(html, content_type="text/html")

    return {"html": html, "owner_name": owner.name, "unit_count": len(owner_rows)}


@router.post("/marketing-report/test-send")
def test_send_email(request, payload: TestSendSchema):
    """Send a test copy of an owner's email to the logged-in staff user."""
    from django.conf import settings as django_settings

    from properties.models import Owner

    from weekly_reports.services.marketing_report import build_marketing_report
    from weekly_reports.services.owner_email_html import build_owner_email_html
    from weekly_reports.services.weekly_update import default_date_range

    if not (hasattr(request, "user") and request.user.is_staff):
        from ninja.errors import HttpError

        raise HttpError(403, "Staff access required")

    if not request.user.email:
        return {"ok": False, "error": "Your account has no email address set"}

    owner = get_object_or_404(Owner, id=payload.owner_id)

    if payload.start and payload.end:
        week_start = date.fromisoformat(payload.start)
        week_end = date.fromisoformat(payload.end)
    else:
        week_start, week_end = default_date_range()

    report = build_marketing_report(week_start, week_end)
    owner_rows = [r for r in report["rows"] if owner.name in (r.get("owner_names") or "")]

    if not owner_rows:
        return {"ok": False, "error": f"No active listings found for {owner.name}"}

    html = build_owner_email_html(owner, owner_rows, report["benchmarks"], week_start, week_end)

    try:
        from django.core.mail import EmailMultiAlternatives, get_connection

        # Always route test sends through SendGrid for real delivery,
        # even when the dev environment defaults to MailHog.
        connection = get_connection("anymail.backends.sendgrid.EmailBackend")

        week_label = (
            f"{week_start.strftime('%b %d')} – {week_end.strftime('%b %d, %Y')}"
        )
        msg = EmailMultiAlternatives(
            subject=f"[TEST] Weekly Leasing Update — {week_label}",
            from_email=django_settings.DEFAULT_FROM_EMAIL,
            to=[request.user.email],
            connection=connection,
        )
        msg.attach_alternative(html, "text/html")
        msg.send()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:500]}

    return {
        "ok": True,
        "sent_to": request.user.email,
        "owner_name": owner.name,
        "unit_count": len(owner_rows),
    }


@router.post("/marketing-report/send-emails")
def send_emails(request, payload: SendEmailsSchema):
    """Send approved weekly leasing emails to owners. Staff-auth only."""
    from weekly_reports.services.send_owner_emails import send_approved_emails

    if not (hasattr(request, "user") and request.user.is_staff):
        from ninja.errors import HttpError
        raise HttpError(403, "Staff access required")

    try:
        week_start = date.fromisoformat(payload.start)
        week_end = date.fromisoformat(payload.end)
    except ValueError as e:
        return {"ok": False, "error": f"Invalid date format: {e}"}

    user = request.user if hasattr(request, "user") and request.user.is_authenticated else None
    result = send_approved_emails(
        week_start=week_start,
        week_end=week_end,
        user=user,
        dry_run=payload.dry_run,
    )
    result["ok"] = True
    return result


@router.get("/marketing-report/send-history")
def send_history(request, start: str = None, end: str = None):
    """Return OwnerEmailSend records for the week."""
    from datetime import timedelta

    from weekly_reports.models import OwnerEmailSend
    from weekly_reports.services.weekly_update import default_date_range

    if start and end:
        week_start = date.fromisoformat(start)
    else:
        week_start, _ = default_date_range()

    monday = week_start - timedelta(days=week_start.weekday())

    sends = OwnerEmailSend.objects.filter(week_date=monday).select_related("owner", "sent_by")
    return [
        {
            "id": s.id,
            "owner_name": s.owner.name,
            "owner_email": s.owner.email,
            "status": s.status,
            "sent_at": s.sent_at.isoformat() if s.sent_at else None,
            "sent_by": (s.sent_by.get_full_name() or s.sent_by.username) if s.sent_by else "",
            "sendgrid_message_id": s.sendgrid_message_id,
            "units_included": s.units_included,
            "error_detail": s.error_detail,
        }
        for s in sends
    ]
