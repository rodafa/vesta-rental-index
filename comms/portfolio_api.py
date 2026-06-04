"""
Portfolio-grain monthly notes API + owner-grain send API.

All new paths — existing /api/reports/owner-notes/ endpoints are untouched.

Portfolio authoring (PortfolioMonthlyNote):
    GET    /api/reports/portfolio-notes?month=YYYY-MM
    PUT    /api/reports/portfolio-notes/{id}
    POST   /api/reports/portfolio-notes/{id}/approve
    POST   /api/reports/portfolio-notes/generate
    POST   /api/reports/portfolio-notes/approve-all
    DELETE /api/reports/portfolio-notes/{id}
    DELETE /api/reports/portfolio-notes/delete-all

Owner-grain send (assembled from PortfolioMonthlyNote):
    GET  /api/reports/owner-sends/recipients?month=YYYY-MM
    GET  /api/reports/owner-sends/recipients/{email}/preview
    POST /api/reports/owner-sends/recipients/{email}/send
    POST /api/reports/owner-sends/recipients/{email}/test-send
    POST /api/reports/owner-sends/send-all
"""

import json
import logging
import threading
import time

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .models import EmailDraft, PortfolioMonthlyNote
from .services import (
    _normalize_email,
    _format_period_label,
    _render_notes_html_from_text,
    assemble_owner_email,
    generate_portfolio_notes,
    get_recipients_for_period,
    send_draft,
    _portfolio_gen_lock,
    _portfolio_gen_started_at,
    _PORTFOLIO_GEN_TTL,
)

logger = logging.getLogger(__name__)

PRODUCT = "monthly_owner_notes"


def _require_access(request):
    """Check login and role access. Returns JsonResponse on failure, None on success."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)
    if not request.user.can_access(PRODUCT):
        return JsonResponse({"error": "Forbidden"}, status=403)
    return None


def _parse_month(request):
    """Extract year/month from ?month=YYYY-MM query param. Returns (year, month) or None."""
    month_str = request.GET.get("month", "")
    if not month_str or len(month_str) < 7:
        return None
    try:
        parts = month_str.split("-")
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None


def _period_from_month(year, month):
    """Return (period_start, period_end) for a YYYY-MM pair."""
    import calendar
    from datetime import date

    _, last_day = calendar.monthrange(year, month)
    return date(year, month, 1), date(year, month, last_day)


def _serialize_portfolio_note(note):
    """Serialize a PortfolioMonthlyNote for the dashboard JS."""
    # Get owners who receive this portfolio
    owners = list(
        note.portfolio.owners.filter(is_active=True)
        .exclude(email="")
        .exclude(email__isnull=True)
        .order_by("name")
        .values("pk", "name", "email")
    )

    # Statement data from the portfolio
    from accounting.models import PortfolioStatement

    latest_stmt = (
        PortfolioStatement.objects.filter(
            portfolio=note.portfolio, status="2"
        )
        .order_by("-period_end")
        .first()
    )

    statement_period = None
    total_income = None
    total_expenses = None
    total_distribution = None
    ending_balance = None

    if latest_stmt:
        statement_period = (
            f"{latest_stmt.period_start.strftime('%b %d')} – "
            f"{latest_stmt.period_end.strftime('%b %d, %Y')}"
        )
        total_income = float(latest_stmt.total_income)
        total_expenses = float(latest_stmt.total_expenses)
        total_distribution = float(latest_stmt.total_distribution)
        ending_balance = float(latest_stmt.ending_balance)

    # Use approved_generated_note if set, else generated_note
    display_note = note.approved_generated_note or note.generated_note
    word_count = len(display_note.split()) if display_note.strip() else 0

    # Property count for this portfolio
    property_count = note.portfolio.properties.filter(is_active=True).count()

    return {
        "id": note.pk,
        "portfolio_id": note.portfolio.pk,
        "portfolio_name": note.portfolio.name,
        "property_count": property_count,
        "status": note.status,
        "generated_note": note.generated_note,
        "approved_generated_note": note.approved_generated_note,
        "display_note": display_note,
        "word_count": word_count,
        "financials_html": note.financials_html,
        "notes_html": note.notes_html,
        "statement_period": statement_period,
        "total_income": total_income,
        "total_expenses": total_expenses,
        "total_distribution": total_distribution,
        "ending_balance": ending_balance,
        "owners": [
            {"id": o["pk"], "name": o["name"], "email": o["email"]}
            for o in owners
        ],
        "generated_at": note.generated_at.isoformat() if note.generated_at else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Portfolio-notes endpoints
# ---------------------------------------------------------------------------


@require_GET
def list_portfolio_notes(request):
    """GET /api/reports/portfolio-notes?month=YYYY-MM"""
    err = _require_access(request)
    if err:
        return err

    parsed = _parse_month(request)
    if not parsed:
        return JsonResponse([], safe=False)

    year, month = parsed
    period_start, _ = _period_from_month(year, month)

    notes = (
        PortfolioMonthlyNote.objects.filter(
            period_type="monthly",
            period_start=period_start,
            portfolio__is_active=True,
        )
        .select_related("portfolio")
        .order_by("portfolio__name")
    )

    return JsonResponse([_serialize_portfolio_note(n) for n in notes], safe=False)


@require_http_methods(["PUT", "DELETE"])
def portfolio_note_detail(request, note_id):
    """PUT or DELETE /api/reports/portfolio-notes/{id}"""
    err = _require_access(request)
    if err:
        return err

    try:
        note = PortfolioMonthlyNote.objects.select_related("portfolio").get(pk=note_id)
    except PortfolioMonthlyNote.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

    if request.method == "DELETE":
        if note.status == "approved":
            return JsonResponse(
                {"error": "Unapprove before deleting"}, status=400
            )
        note.delete()
        return JsonResponse({}, status=200)

    # PUT — update the note text, re-render notes_html
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    new_note = body.get("generated_note", "")

    # If note is approved, writes go to approved_generated_note
    if note.status == "approved":
        note.approved_generated_note = new_note
        note.notes_html = _render_notes_html_from_text(new_note)
        note.save(update_fields=["approved_generated_note", "notes_html", "updated_at"])
    else:
        note.generated_note = new_note
        note.notes_html = _render_notes_html_from_text(new_note)
        note.save(update_fields=["generated_note", "notes_html", "updated_at"])

    return JsonResponse(_serialize_portfolio_note(note))


@require_POST
def approve_portfolio_note(request, note_id):
    """POST /api/reports/portfolio-notes/{id}/approve"""
    err = _require_access(request)
    if err:
        return err

    try:
        note = PortfolioMonthlyNote.objects.select_related("portfolio").get(pk=note_id)
    except PortfolioMonthlyNote.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

    # Snapshot: copy generated_note to approved_generated_note if not yet edited
    if not note.approved_generated_note:
        note.approved_generated_note = note.generated_note

    note.status = "approved"
    note.save(update_fields=["status", "approved_generated_note", "updated_at"])

    return JsonResponse(_serialize_portfolio_note(note))


@require_POST
def generate_portfolio_notes_api(request):
    """POST /api/reports/portfolio-notes/generate — background generation."""
    import comms.services as svc

    err = _require_access(request)
    if err:
        return err

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    start_str = body.get("start_date", "")
    end_str = body.get("end_date", "")
    if not start_str or not end_str:
        return JsonResponse(
            {"ok": False, "error": "start_date and end_date required"}, status=400
        )

    from datetime import date

    try:
        period_start = date.fromisoformat(start_str)
        period_end = date.fromisoformat(end_str)
    except ValueError:
        return JsonResponse(
            {"ok": False, "error": "Invalid date format (YYYY-MM-DD)"}, status=400
        )

    is_dry_run = body.get("dry_run", False)

    # Concurrency guard with TTL auto-expiry
    with svc._portfolio_gen_lock:
        if svc._portfolio_gen_started_at is not None:
            elapsed = time.monotonic() - svc._portfolio_gen_started_at
            if elapsed < svc._PORTFOLIO_GEN_TTL:
                return JsonResponse(
                    {"ok": False, "error": "Generation already in progress"}, status=409
                )
            logger.warning(
                "comms_portfolio_gen_lock_expired",
                extra={"elapsed_seconds": round(elapsed)},
            )
        svc._portfolio_gen_started_at = time.monotonic()

    # Build portfolio queryset
    from core.models import Portfolio

    portfolios = Portfolio.objects.filter(is_active=True).order_by("name")

    portfolio_name = body.get("portfolio_name", "").strip()
    if portfolio_name:
        portfolios = portfolios.filter(name__icontains=portfolio_name)

    def _run():
        try:
            generate_portfolio_notes(
                portfolios,
                period_start,
                period_end,
                dry_run=is_dry_run,
            )
        except Exception:
            logger.exception("comms_portfolio_gen_background_error")
        finally:
            svc._portfolio_gen_started_at = None

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return JsonResponse({"ok": True})


@require_POST
def approve_all_portfolio_notes(request):
    """POST /api/reports/portfolio-notes/approve-all?month=YYYY-MM"""
    err = _require_access(request)
    if err:
        return err

    parsed = _parse_month(request)
    if not parsed:
        return JsonResponse({"error": "month param required"}, status=400)

    year, month = parsed
    period_start, _ = _period_from_month(year, month)

    notes = PortfolioMonthlyNote.objects.filter(
        period_type="monthly",
        period_start=period_start,
        status="draft",
    )

    # Snapshot approved_generated_note for each
    count = 0
    for note in notes:
        if not note.approved_generated_note:
            note.approved_generated_note = note.generated_note
        note.status = "approved"
        note.save(update_fields=["status", "approved_generated_note", "updated_at"])
        count += 1

    return JsonResponse({"approved": count})


@require_POST
def delete_all_portfolio_notes(request):
    """DELETE /api/reports/portfolio-notes/delete-all?month=YYYY-MM"""
    err = _require_access(request)
    if err:
        return err

    parsed = _parse_month(request)
    if not parsed:
        return JsonResponse({"error": "month param required"}, status=400)

    year, month = parsed
    period_start, _ = _period_from_month(year, month)

    qs = PortfolioMonthlyNote.objects.filter(
        period_type="monthly",
        period_start=period_start,
        status="draft",
    )

    count = qs.count()
    qs.delete()

    return JsonResponse({"deleted": count})


# ---------------------------------------------------------------------------
# Owner-sends endpoints
# ---------------------------------------------------------------------------


@require_GET
def list_recipients(request):
    """GET /api/reports/owner-sends/recipients?month=YYYY-MM"""
    err = _require_access(request)
    if err:
        return err

    parsed = _parse_month(request)
    if not parsed:
        return JsonResponse([], safe=False)

    year, month = parsed
    period_start, _ = _period_from_month(year, month)

    recipients = get_recipients_for_period(period_start, period_type="monthly")
    return JsonResponse(recipients, safe=False)


@require_GET
def preview_recipient(request, email):
    """GET /api/reports/owner-sends/recipients/{email}/preview"""
    err = _require_access(request)
    if err:
        return err

    parsed = _parse_month(request)
    if not parsed:
        return JsonResponse({"error": "month param required"}, status=400)

    year, month = parsed
    period_start, _ = _period_from_month(year, month)

    assembled = assemble_owner_email(email, period_start, period_type="monthly")
    return JsonResponse(assembled)


@require_POST
def send_recipient(request, email):
    """POST /api/reports/owner-sends/recipients/{email}/send — real send."""
    err = _require_access(request)
    if err:
        return err

    parsed = _parse_month(request)
    if not parsed:
        return JsonResponse({"error": "month param required"}, status=400)

    year, month = parsed
    period_start, period_end = _period_from_month(year, month)

    norm_email = _normalize_email(email)

    # Assemble the email
    assembled = assemble_owner_email(norm_email, period_start, period_type="monthly")

    if not assembled["all_approved"]:
        gating = [
            p["name"] for p in assembled["portfolios"]
            if p["status"] != "approved"
        ]
        return JsonResponse({
            "ok": False,
            "error": "Not all portfolios are approved",
            "gating_portfolios": gating,
        }, status=400)

    if not assembled["financials_html"] and not assembled["notes_html"]:
        return JsonResponse({
            "ok": False,
            "error": "No content to send",
        }, status=400)

    # Upsert EmailDraft as the send/audit record
    from core.models import Owner

    rep_owner = Owner.objects.filter(
        is_active=True,
        email__iexact=norm_email,
    ).order_by("pk").first()

    if not rep_owner:
        return JsonResponse({
            "ok": False,
            "error": f"No active owner found for {norm_email}",
        }, status=400)

    period_label = _format_period_label("monthly", period_start, period_end)
    subject = f"Monthly Owner Update — {period_label}"

    body_html = json.dumps({
        "financials_html": assembled["financials_html"],
        "notes_html": assembled["notes_html"],
    })

    draft, _ = EmailDraft.objects.update_or_create(
        product=PRODUCT,
        recipient_email=norm_email,
        period_type="monthly",
        period_start=period_start,
        defaults={
            "owner": rep_owner,
            "subject": subject,
            "body_html": body_html,
            "generated_note": "",
            "period_end": period_end,
            "status": "approved",
            "sent_at": None,
            "sent_by": None,
        },
    )

    try:
        result = send_draft(draft, request.user)
        return JsonResponse({
            "ok": True,
            "message": "Email sent.",
            "recipient": norm_email,
            "draft_id": draft.pk,
            "sendgrid_status": result.get("sendgrid_status"),
        })
    except (PermissionError, ValueError) as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=400)
    except Exception as exc:
        logger.exception(
            "comms_owner_send_failed",
            extra={"email": norm_email},
        )
        return JsonResponse(
            {"ok": False, "message": f"Send failed: {exc}"}, status=500
        )


@require_POST
def test_send_recipient(request, email):
    """POST /api/reports/owner-sends/recipients/{email}/test-send — transient test."""
    err = _require_access(request)
    if err:
        return err

    parsed = _parse_month(request)
    if not parsed:
        return JsonResponse({"error": "month param required"}, status=400)

    year, month = parsed
    period_start, period_end = _period_from_month(year, month)

    norm_email = _normalize_email(email)

    # Optional override recipient
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        body = {}

    test_to = body.get("to", "").strip() or request.user.email

    # Assemble transiently
    assembled = assemble_owner_email(norm_email, period_start, period_type="monthly")

    if not assembled["financials_html"] and not assembled["notes_html"]:
        return JsonResponse({
            "ok": False,
            "error": "No content to preview",
        }, status=400)

    # Build a transient Mail object — NO EmailDraft, NO CC, [TEST] subject
    import sendgrid as sg_module
    from sendgrid.helpers.mail import Mail

    period_label = _format_period_label("monthly", period_start, period_end)
    subject = f"[TEST] Monthly Owner Update — {period_label}"

    template_id = getattr(settings, "COMMS_SENDGRID_MONTHLY_TEMPLATE_ID", "")

    message = Mail(
        from_email=settings.COMMS_FROM_EMAIL,
        to_emails=test_to,
        subject=subject,
    )
    message.template_id = template_id
    message.dynamic_template_data = {
        "owner_name": assembled["owner_name"],
        "financials_html": assembled["financials_html"],
        "notes_html": assembled["notes_html"],
    }

    sg_client = sg_module.SendGridAPIClient(settings.SENDGRID_API_KEY)
    try:
        response = sg_client.send(message)
    except Exception as exc:
        logger.exception(
            "comms_test_send_failed",
            extra={"email": norm_email, "test_to": test_to},
        )
        return JsonResponse(
            {"ok": False, "message": f"Test send failed: {exc}"}, status=500
        )

    status_code = response.status_code
    if status_code < 200 or status_code >= 300:
        return JsonResponse(
            {"ok": False, "message": f"SendGrid returned {status_code}"}, status=500
        )

    logger.info(
        "comms_test_send_ok",
        extra={
            "email": norm_email,
            "test_to": test_to,
            "sendgrid_status": status_code,
        },
    )

    return JsonResponse({
        "ok": True,
        "message": f"Test email sent to {test_to}",
        "sendgrid_status": status_code,
    })


@require_POST
def send_all_ready(request):
    """POST /api/reports/owner-sends/send-all?month=YYYY-MM — send all ready recipients."""
    err = _require_access(request)
    if err:
        return err

    parsed = _parse_month(request)
    if not parsed:
        return JsonResponse({"error": "month param required"}, status=400)

    year, month = parsed
    period_start, period_end = _period_from_month(year, month)

    recipients = get_recipients_for_period(period_start, period_type="monthly")

    sent = 0
    failed = 0
    skipped = 0

    for recip in recipients:
        if recip["is_sent"]:
            skipped += 1
            continue
        if not recip["all_approved"]:
            skipped += 1
            continue

        norm_email = recip["recipient_email"]

        try:
            assembled = assemble_owner_email(norm_email, period_start, period_type="monthly")

            if not assembled["financials_html"] and not assembled["notes_html"]:
                skipped += 1
                continue

            from core.models import Owner

            rep_owner = Owner.objects.filter(
                is_active=True,
                email__iexact=norm_email,
            ).order_by("pk").first()

            if not rep_owner:
                skipped += 1
                continue

            period_label = _format_period_label("monthly", period_start, period_end)
            subject = f"Monthly Owner Update — {period_label}"

            body_html = json.dumps({
                "financials_html": assembled["financials_html"],
                "notes_html": assembled["notes_html"],
            })

            draft, _ = EmailDraft.objects.update_or_create(
                product=PRODUCT,
                recipient_email=norm_email,
                period_type="monthly",
                period_start=period_start,
                defaults={
                    "owner": rep_owner,
                    "subject": subject,
                    "body_html": body_html,
                    "generated_note": "",
                    "period_end": period_end,
                    "status": "approved",
                    "sent_at": None,
                    "sent_by": None,
                },
            )

            send_draft(draft, request.user)
            sent += 1

        except Exception:
            logger.exception(
                "comms_send_all_ready_failed",
                extra={"email": norm_email},
            )
            failed += 1

    return JsonResponse({"sent": sent, "failed": failed, "skipped": skipped})
