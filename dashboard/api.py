import logging
from datetime import date, timedelta
from typing import Optional

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from ninja import Router, Schema

from .models import OwnerReportNote, PropertyWeeklyNote, UnitNote

router = Router(tags=["Dashboard"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _note_to_dict(note):
    return {
        "id": note.id,
        "owner_id": note.owner_id,
        "owner_name": note.owner.name,
        "status": note.status,
        "notes_text": note.notes_text,
        "email_body": note.email_body,
        "email_subject": note.email_subject,
        "report_date": note.report_date,
        "sent_at": note.sent_at.isoformat() if note.sent_at else None,
        "opened_at": note.opened_at.isoformat() if note.opened_at else None,
        "properties_included": note.properties_included,
        "updated_at": note.updated_at.isoformat(),
    }


def _prop_note_to_dict(note):
    return {
        "id": note.id,
        "unit_id": note.unit_id,
        "week_date": note.week_date.isoformat(),
        "author": note.author,
        "note_text": note.note_text,
        "created_at": note.created_at.isoformat(),
        "updated_at": note.updated_at.isoformat(),
    }


def _render_owner_email(owner, report_data, note):
    """Render the HTML email for an owner report using the Django template."""
    from django.db.models import Sum
    from django.db.models.functions import Coalesce
    from market.models import DailyLeasingSummary, DailyUnitSnapshot

    week_date = note.report_date
    week_end = week_date + timedelta(days=6)
    summary_text = note.email_body or note.notes_text or ""

    # Gather property notes for this week
    unit_ids = [u["unit_id"] for u in report_data.get("units", [])]
    notes_map = {}
    for pn in PropertyWeeklyNote.objects.filter(unit_id__in=unit_ids, week_date=week_date):
        notes_map[pn.unit_id] = pn.note_text

    # Attach notes, WoW deltas, and avg/wk for the template
    units_with_notes = []
    for u in report_data.get("units", []):
        u_copy = dict(u)
        u_copy["property_note"] = notes_map.get(u["unit_id"], "")
        w = u.get("weekly", {})
        p = u.get("prev_weekly", {})
        u_copy["leads_delta"] = w.get("leads", 0) - p.get("leads", 0)
        u_copy["showings_delta"] = w.get("showings", 0) - p.get("showings", 0)
        u_copy["apps_delta"] = w.get("apps", 0) - p.get("apps", 0)

        # Avg per week (all-time / weeks active)
        dom = u.get("days_on_market") or 0
        weeks = max(dom / 7.0, 1)
        a = u.get("all_time", {})
        u_copy["avg_leads_per_week"] = round(a.get("leads", 0) / weeks, 1)
        u_copy["avg_showings_per_week"] = round(a.get("showings", 0) / weeks, 1)
        u_copy["avg_apps_per_week"] = round(a.get("apps", 0) / weeks, 1)

        # Formatted price string with commas
        price = u.get("current_list_price")
        u_copy["formatted_list_price"] = "${:,.2f}".format(float(price)) if price else None

        units_with_notes.append(u_copy)

    # ── Global market data (all active Vesta listings) ────────────────────
    latest_date = (
        DailyUnitSnapshot.objects.order_by("-snapshot_date")
        .values_list("snapshot_date", flat=True)
        .first()
    )
    global_dom_map = {}
    if latest_date:
        for snap in DailyUnitSnapshot.objects.filter(
            snapshot_date=latest_date, status="active"
        ).values("unit_id", "days_on_market"):
            global_dom_map[snap["unit_id"]] = snap["days_on_market"] or 0

    active_count = len(global_dom_map)

    # All-time leasing for all active units
    global_leasing = {}
    if global_dom_map:
        for row in DailyLeasingSummary.objects.filter(
            unit_id__in=global_dom_map.keys()
        ).values("unit_id").annotate(
            leads=Coalesce(Sum("leads_count"), 0),
            showings=Coalesce(Sum("showings_completed_count"), 0),
            apps=Coalesce(Sum("applications_count"), 0),
        ):
            global_leasing[row["unit_id"]] = row

    # Compute global totals and market averages
    g_total_leads = 0
    g_total_showings = 0
    g_total_apps = 0
    per_unit_lpw = []
    per_unit_spw = []
    g_dom_vals = []
    for uid, dom in global_dom_map.items():
        gl = global_leasing.get(uid, {})
        leads = gl.get("leads", 0)
        showings = gl.get("showings", 0)
        apps = gl.get("apps", 0)
        g_total_leads += leads
        g_total_showings += showings
        g_total_apps += apps
        if dom:
            g_dom_vals.append(dom)
        weeks = max(dom / 7.0, 1)
        per_unit_lpw.append(leads / weeks)
        per_unit_spw.append(showings / weeks)

    g_avg_dom = round(sum(g_dom_vals) / len(g_dom_vals), 1) if g_dom_vals else 0
    g_lts = round(g_total_showings / g_total_leads * 100, 1) if g_total_leads else 0
    g_sta = round(g_total_apps / g_total_showings * 100, 1) if g_total_showings else 0
    mkt_avg_lpw = round(sum(per_unit_lpw) / len(per_unit_lpw), 1) if per_unit_lpw else 0
    mkt_avg_spw = round(sum(per_unit_spw) / len(per_unit_spw), 1) if per_unit_spw else 0

    context = {
        "owner_name": owner.name.split()[0] if owner.name else "Owner",
        "owner_full_name": owner.name or "Owner",
        "report_date": week_date,
        "week_end_date": week_end,
        "summary_text": summary_text,
        "units": units_with_notes,
        "portfolio_totals": {
            "total_leads": g_total_leads,
            "total_showings": g_total_showings,
            "total_apps": g_total_apps,
            "avg_dom": g_avg_dom,
            "lead_to_show_pct": g_lts,
            "show_to_app_pct": g_sta,
            "listing_count": active_count,
        },
        "market_avg": {
            "leads_per_week": mkt_avg_lpw,
            "showings_per_week": mkt_avg_spw,
            "avg_dom": g_avg_dom,
            "active_count": active_count,
        },
    }
    return render_to_string("emails/owner_report.html", context)


def _fetch_report_data(owner_id, week_date):
    """Fetch owner report data by calling the analytics logic directly."""
    from analytics.api import owner_report_detail

    class FakeRequest:
        pass

    result = owner_report_detail(FakeRequest(), owner_id, week_date=week_date)
    # ninja endpoint returns dict or schema — normalize
    if hasattr(result, "dict"):
        return result.dict()
    return result


# ---------------------------------------------------------------------------
# Owner Report Note Schemas
# ---------------------------------------------------------------------------


class OwnerReportNoteSchema(Schema):
    id: int
    owner_id: int
    owner_name: str
    status: str
    notes_text: str
    email_body: str
    email_subject: str
    report_date: date
    sent_at: Optional[str] = None
    opened_at: Optional[str] = None
    properties_included: list = []
    updated_at: str


class OwnerReportNoteCreateSchema(Schema):
    owner_id: int
    report_date: date
    notes_text: str = ""
    email_body: str = ""
    email_subject: str = ""
    status: str = "draft"


class OwnerReportNoteUpdateSchema(Schema):
    notes_text: Optional[str] = None
    email_body: Optional[str] = None
    email_subject: Optional[str] = None
    status: Optional[str] = None


# ---------------------------------------------------------------------------
# Owner Report Note Endpoints
# ---------------------------------------------------------------------------


@router.get("/owner-notes", response=list[OwnerReportNoteSchema])
def list_owner_notes(
    request,
    owner_id: Optional[int] = None,
    report_date: Optional[date] = None,
    status: Optional[str] = None,
):
    qs = OwnerReportNote.objects.select_related("owner").all()
    if owner_id:
        qs = qs.filter(owner_id=owner_id)
    if report_date:
        qs = qs.filter(report_date=report_date)
    if status:
        qs = qs.filter(status=status)
    return [_note_to_dict(n) for n in qs]


@router.post("/owner-notes", response=OwnerReportNoteSchema)
def create_owner_note(request, data: OwnerReportNoteCreateSchema):
    note, created = OwnerReportNote.objects.update_or_create(
        owner_id=data.owner_id,
        report_date=data.report_date,
        defaults={
            "notes_text": data.notes_text,
            "email_body": data.email_body,
            "email_subject": data.email_subject,
            "status": data.status,
        },
    )
    return _note_to_dict(
        OwnerReportNote.objects.select_related("owner").get(pk=note.pk)
    )


@router.put("/owner-notes/{note_id}", response=OwnerReportNoteSchema)
def update_owner_note(request, note_id: int, data: OwnerReportNoteUpdateSchema):
    note = get_object_or_404(
        OwnerReportNote.objects.select_related("owner"), pk=note_id
    )
    if data.notes_text is not None:
        note.notes_text = data.notes_text
    if data.email_body is not None:
        note.email_body = data.email_body
    if data.email_subject is not None:
        note.email_subject = data.email_subject
    if data.status is not None:
        note.status = data.status
    note.save()
    return _note_to_dict(note)


@router.post("/owner-notes/{note_id}/preview")
def preview_owner_email(request, note_id: int):
    """Render the HTML email preview for an owner report."""
    note = get_object_or_404(
        OwnerReportNote.objects.select_related("owner"), pk=note_id
    )
    report_data = _fetch_report_data(note.owner_id, note.report_date)
    html = _render_owner_email(note.owner, report_data, note)
    return {"html": html}


@router.post("/owner-notes/{note_id}/send", response=OwnerReportNoteSchema)
def send_owner_note(request, note_id: int):
    """Send owner report HTML email via SendGrid with BCC + open tracking."""
    from django.core.mail import EmailMessage

    note = get_object_or_404(
        OwnerReportNote.objects.select_related("owner"), pk=note_id
    )

    owner = note.owner
    recipient = owner.email
    if not recipient:
        return {"status": "error", "detail": "Owner has no email address"}

    # Fetch fresh data and render HTML
    report_data = _fetch_report_data(owner.id, note.report_date)
    html_body = _render_owner_email(owner, report_data, note)

    subject = note.email_subject or f"Weekly Vacancy Update — {note.report_date.strftime('%b %d, %Y')}"
    unit_ids = [u["unit_id"] for u in report_data.get("units", [])]

    try:
        email = EmailMessage(
            subject=subject,
            body=html_body,
            to=[recipient],
            bcc=["rodrigo@vestapm.com"],
        )
        email.content_subtype = "html"

        # SendGrid open tracking via anymail esp_extra
        if hasattr(settings, "ANYMAIL"):
            email.esp_extra = {
                "tracking_settings": {
                    "open_tracking": {"enable": True},
                },
            }

        email.send()
        logger.info("Owner report email sent to %s (%s)", owner.name, recipient)

        # Try to capture SendGrid message ID
        msg_id = ""
        if hasattr(email, "anymail_status"):
            msg_id = getattr(email.anymail_status, "message_id", "") or ""

    except Exception:
        logger.exception("Failed to send owner report email to %s", recipient)
        return {"status": "error", "detail": "Email delivery failed"}

    note.status = "sent"
    note.sent_at = timezone.now()
    note.properties_included = unit_ids
    note.sendgrid_message_id = msg_id
    note.save(update_fields=[
        "status", "sent_at", "properties_included", "sendgrid_message_id",
    ])
    return _note_to_dict(note)


# ---------------------------------------------------------------------------
# Property Weekly Notes
# ---------------------------------------------------------------------------


class PropertyWeeklyNoteSchema(Schema):
    id: int
    unit_id: int
    week_date: str
    author: str
    note_text: str
    created_at: str
    updated_at: str


class PropertyWeeklyNoteCreateSchema(Schema):
    unit_id: int
    week_date: date
    author: str
    note_text: str


@router.get("/property-notes", response=list[PropertyWeeklyNoteSchema])
def list_property_notes(
    request,
    unit_id: Optional[int] = None,
    week_date: Optional[date] = None,
):
    qs = PropertyWeeklyNote.objects.all()
    if unit_id:
        qs = qs.filter(unit_id=unit_id)
    if week_date:
        qs = qs.filter(week_date=week_date)
    return [_prop_note_to_dict(n) for n in qs]


@router.post("/property-notes", response=PropertyWeeklyNoteSchema)
def create_property_note(request, data: PropertyWeeklyNoteCreateSchema):
    note, _ = PropertyWeeklyNote.objects.update_or_create(
        unit_id=data.unit_id,
        week_date=data.week_date,
        defaults={
            "author": data.author,
            "note_text": data.note_text,
        },
    )
    return _prop_note_to_dict(note)


# ---------------------------------------------------------------------------
# Unit Notes — staff notes on individual units
# ---------------------------------------------------------------------------


class UnitNoteSchema(Schema):
    id: int
    unit_id: int
    author: str
    note_text: str
    created_at: str
    updated_at: str


class UnitNoteCreateSchema(Schema):
    unit_id: int
    author: str
    note_text: str


class UnitNoteUpdateSchema(Schema):
    note_text: Optional[str] = None
    author: Optional[str] = None


def _unit_note_to_dict(note):
    return {
        "id": note.id,
        "unit_id": note.unit_id,
        "author": note.author,
        "note_text": note.note_text,
        "created_at": note.created_at.isoformat(),
        "updated_at": note.updated_at.isoformat(),
    }


@router.get("/unit-notes", response=list[UnitNoteSchema])
def list_unit_notes(request, unit_id: Optional[int] = None):
    qs = UnitNote.objects.all()
    if unit_id:
        qs = qs.filter(unit_id=unit_id)
    return [_unit_note_to_dict(n) for n in qs]


@router.post("/unit-notes", response=UnitNoteSchema)
def create_unit_note(request, data: UnitNoteCreateSchema):
    note = UnitNote.objects.create(
        unit_id=data.unit_id,
        author=data.author,
        note_text=data.note_text,
    )
    return _unit_note_to_dict(note)


@router.put("/unit-notes/{note_id}", response=UnitNoteSchema)
def update_unit_note(request, note_id: int, data: UnitNoteUpdateSchema):
    note = get_object_or_404(UnitNote, pk=note_id)
    if data.note_text is not None:
        note.note_text = data.note_text
    if data.author is not None:
        note.author = data.author
    note.save()
    return _unit_note_to_dict(note)


@router.delete("/unit-notes/{note_id}")
def delete_unit_note(request, note_id: int):
    note = get_object_or_404(UnitNote, pk=note_id)
    note.delete()
    return {"success": True}
