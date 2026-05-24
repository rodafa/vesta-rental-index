import io
import logging
import threading
from datetime import date

from django.core.management import call_command
from django.http import HttpResponse
from ninja import Router, Schema

from maintenance.models import MaintenanceEmailSend, Meld
from maintenance.services.meld_summarizer import batch_summarize
from maintenance.services.owner_email_builder import (
    build_maintenance_email_data,
    build_maintenance_email_html,
)
from maintenance.services.send_maintenance_emails import send_maintenance_emails
from properties.models import Owner

logger = logging.getLogger(__name__)

router = Router(tags=["Maintenance"])


# ---------------------------------------------------------------------------
# Existing daily summary endpoints
# ---------------------------------------------------------------------------


def _run_daily_summary():
    """Run the daily summary command in a background thread."""
    try:
        call_command("property_meld_daily_summary")
    except Exception:
        logger.exception("Error running property_meld_daily_summary")


@router.post("/daily-summary/trigger")
def trigger_daily_summary(request):
    """Trigger the Property Meld daily maintenance summary Slack post."""
    threading.Thread(target=_run_daily_summary, daemon=True).start()
    return {"status": "started", "message": "Daily summary posting to Slack"}


@router.post("/daily-summary/debug")
def debug_daily_summary(request):
    """Run daily summary synchronously and return output + errors for debugging."""
    buf = io.StringIO()
    err = io.StringIO()
    try:
        call_command("property_meld_daily_summary", stdout=buf, stderr=err)
        return {"status": "ok", "output": buf.getvalue(), "errors": err.getvalue()}
    except Exception as exc:
        return {"status": "error", "output": buf.getvalue(), "errors": err.getvalue(), "exception": str(exc)}


# ---------------------------------------------------------------------------
# Owner maintenance email endpoints (staff-auth gated)
# ---------------------------------------------------------------------------


def _staff_required(request):
    if not request.user.is_authenticated or not request.user.is_staff:
        return False
    return True


class MeldListQuery(Schema):
    owner_id: int
    start: date
    end: date


class SummaryEditBody(Schema):
    summary: str


class GenerateSummariesBody(Schema):
    meld_ids: list[int] | None = None
    force: bool = False


class SendBody(Schema):
    start: date
    end: date
    owner_id: int | None = None


class TestSendBody(Schema):
    start: date
    end: date
    owner_id: int
    recipient: str


class SendHistoryQuery(Schema):
    start: date | None = None
    end: date | None = None


@router.get("/owner-email/melds")
def get_owner_email_melds(request, owner_id: int, start: date, end: date):
    """Get melds that would appear in a maintenance email for an owner."""
    if not _staff_required(request):
        return HttpResponse(status=403)

    try:
        owner = Owner.objects.get(pk=owner_id)
    except Owner.DoesNotExist:
        return {"error": "Owner not found"}, 404

    data = build_maintenance_email_data(owner, start, end)
    return {
        "owner_id": owner.pk,
        "owner_name": owner.name,
        "date_start": start.isoformat(),
        "date_end": end.isoformat(),
        "open_melds": data["open_melds"],
        "closed_melds": data["closed_melds"],
        "canceled_melds": data["canceled_melds"],
        "total_spend": float(data["total_spend"]),
    }


@router.get("/owner-email/preview")
def preview_owner_email(request, owner_id: int, start: date, end: date):
    """Render and return the HTML email preview for an owner."""
    if not _staff_required(request):
        return HttpResponse(status=403)

    try:
        owner = Owner.objects.get(pk=owner_id)
    except Owner.DoesNotExist:
        return HttpResponse("Owner not found", status=404)

    data = build_maintenance_email_data(owner, start, end)
    html = build_maintenance_email_html(owner, data, start, end)
    return HttpResponse(html, content_type="text/html")


@router.post("/owner-email/summary/{meld_pk}/edit")
def edit_meld_summary(request, meld_pk: int, body: SummaryEditBody):
    """Edit a meld's staff summary (overrides AI summary in email)."""
    if not _staff_required(request):
        return HttpResponse(status=403)

    try:
        meld = Meld.objects.get(pk=meld_pk)
    except Meld.DoesNotExist:
        return {"error": "Meld not found"}, 404

    meld.staff_summary = body.summary
    meld.summary_status = "edited"
    meld.save(update_fields=["staff_summary", "summary_status", "updated_at"])
    return {"status": "ok", "meld_id": meld.pk, "summary_status": "edited"}


@router.post("/owner-email/generate-summaries")
def generate_summaries(request, body: GenerateSummariesBody):
    """Generate AI summaries for melds (batch)."""
    if not _staff_required(request):
        return HttpResponse(status=403)

    result = batch_summarize(meld_ids=body.meld_ids, force=body.force)
    return result


@router.post("/owner-email/send")
def send_emails(request, body: SendBody):
    """Send maintenance emails to owners."""
    if not _staff_required(request):
        return HttpResponse(status=403)

    result = send_maintenance_emails(
        date_start=body.start,
        date_end=body.end,
        user=request.user,
        owner_id=body.owner_id,
    )
    return result


@router.post("/owner-email/test-send")
def test_send_email(request, body: TestSendBody):
    """Send a test maintenance email to a specific recipient."""
    if not _staff_required(request):
        return HttpResponse(status=403)

    result = send_maintenance_emails(
        date_start=body.start,
        date_end=body.end,
        user=request.user,
        test_recipient=body.recipient,
        owner_id=body.owner_id,
    )
    return result


@router.get("/owner-email/send-history")
def send_history(request, start: date | None = None, end: date | None = None):
    """Get maintenance email send history."""
    if not _staff_required(request):
        return HttpResponse(status=403)

    qs = MaintenanceEmailSend.objects.select_related("owner", "sent_by").order_by(
        "-created_at"
    )
    if start:
        qs = qs.filter(week_date__gte=start)
    if end:
        qs = qs.filter(week_date__lte=end)

    return [
        {
            "id": record.pk,
            "owner_id": record.owner_id,
            "owner_name": record.owner.name,
            "week_date": record.week_date.isoformat(),
            "status": record.status,
            "sent_at": record.sent_at.isoformat() if record.sent_at else None,
            "sent_by": record.sent_by.username if record.sent_by else None,
            "melds_count": len(record.melds_included),
        }
        for record in qs[:100]
    ]
