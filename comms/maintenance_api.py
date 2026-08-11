"""
Maintenance-notes API — weekly portfolio-grain notes + owner-grain send.

Fork of portfolio_api.py adapted for PortfolioMaintenanceNote (weekly period).

Read/view endpoints accept ?start_date=&end_date= or ?week=YYYY-MM-DD:
    GET    /api/reports/maintenance-notes?start_date=&end_date= or ?week=
    GET    /api/reports/maintenance-notes/progress?start_date=&end_date= or ?week=

Mutation/send endpoints require ?week=YYYY-MM-DD (Monday token only):
    PUT    /api/reports/maintenance-notes/{id}
    POST   /api/reports/maintenance-notes/{id}/approve
    POST   /api/reports/maintenance-notes/generate
    POST   /api/reports/maintenance-notes/approve-all?week=YYYY-MM-DD
    POST   /api/reports/maintenance-notes/delete-all?week=YYYY-MM-DD
    DELETE /api/reports/maintenance-notes/{id}

Owner-grain send (standard week only, ?week=YYYY-MM-DD):
    GET  /api/reports/maintenance-sends/recipients?week=YYYY-MM-DD
    GET  /api/reports/maintenance-sends/recipients/{email}/preview?week=YYYY-MM-DD
    POST /api/reports/maintenance-sends/recipients/{email}/send?week=YYYY-MM-DD
    POST /api/reports/maintenance-sends/recipients/{email}/test-send?week=YYYY-MM-DD
    POST /api/reports/maintenance-sends/send-all?week=YYYY-MM-DD
"""

import copy
import json
import logging
import threading
import time
from datetime import date, timedelta

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .maintenance_services import (
    get_maintenance_recipients_for_period,
    note_has_sendable_content,
    _maintenance_gen_lock,
    _maintenance_gen_started_at,
    _MAINTENANCE_GEN_TTL,
)
from .models import EmailDraft, PortfolioMaintenanceNote
from .services import (
    _normalize_email,
    _format_period_label,
    assemble_owner_maintenance_email,
    generate_portfolio_maintenance_notes,
    get_portfolio_generation_scope,
    render_maintenance_notes_html_from_snapshot,
    send_draft,
)

logger = logging.getLogger(__name__)

PRODUCT = "maintenance"


def _require_access(request):
    """Check login and role access. Returns JsonResponse on failure, None on success."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)
    if not request.user.can_access(PRODUCT):
        return JsonResponse({"error": "Forbidden"}, status=403)
    return None


def _parse_week(request):
    """Extract period_start from ?week=YYYY-MM-DD. Returns a date (Monday) or None."""
    week_str = request.GET.get("week", "")
    if not week_str:
        return None
    try:
        d = date.fromisoformat(week_str)
    except ValueError:
        return None
    if d.weekday() != 0:  # Monday = 0
        return None
    return d


def _resolve_period(request):
    """Resolve view period from explicit range or Monday week token.

    Returns (period_start, period_end, explicit) or None.
    explicit=True: start_date+end_date params, passed through unchanged.
    explicit=False: Monday week token, resolved to Mon-Sun pair.
    Used by list + progress endpoints only.
    """
    start_str = request.GET.get("start_date", "")
    end_str = request.GET.get("end_date", "")
    if start_str and end_str:
        try:
            s = date.fromisoformat(start_str)
            e = date.fromisoformat(end_str)
        except ValueError:
            return None
        logger.info(
            "comms_maintenance_explicit_range",
            extra={"start_date": start_str, "end_date": end_str},
        )
        return (s, e, True)

    d = _parse_week(request)
    if d is None:
        return None
    return (d, d + timedelta(days=6), False)


def _notes_for_period(period_start, period_end, explicit):
    """Return PortfolioMaintenanceNote queryset for the resolved period.

    Standard week (explicit=False): exact match on period_start only
    (today's behaviour — period_end is not checked).
    Explicit range (explicit=True): exact match on BOTH period_start
    and period_end, so only notes from that generation run appear.
    """
    qs = PortfolioMaintenanceNote.objects.filter(
        period_type="weekly",
        period_start=period_start,
        portfolio__is_active=True,
    )
    if explicit:
        qs = qs.filter(period_end=period_end)
    return qs


def _serialize_maintenance_note(note):
    """Serialize a PortfolioMaintenanceNote for the dashboard JS."""
    owners = list(
        note.portfolio.owners.filter(is_active=True)
        .exclude(email="")
        .exclude(email__isnull=True)
        .order_by("name")
        .values("pk", "name", "email")
    )

    display_note = note.approved_generated_note or note.generated_note
    word_count = len(display_note.split()) if display_note.strip() else 0

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
        "notes_html": note.notes_html,
        "work_order_snapshot": note.work_order_snapshot,
        "owners": [
            {"id": o["pk"], "name": o["name"], "email": o["email"]}
            for o in owners
        ],
        "generated_at": note.generated_at.isoformat() if note.generated_at else None,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Maintenance-notes endpoints (Layer 1)
# ---------------------------------------------------------------------------


@require_GET
def list_maintenance_notes(request):
    """GET /api/reports/maintenance-notes?week=YYYY-MM-DD
    or  ?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD"""
    err = _require_access(request)
    if err:
        return err

    resolved = _resolve_period(request)
    if not resolved:
        return JsonResponse([], safe=False)

    period_start, period_end, explicit = resolved

    notes = (
        _notes_for_period(period_start, period_end, explicit)
        .select_related("portfolio")
        .order_by("portfolio__name")
    )

    return JsonResponse([_serialize_maintenance_note(n) for n in notes], safe=False)


@require_http_methods(["PUT", "DELETE"])
def maintenance_note_detail(request, note_id):
    """PUT or DELETE /api/reports/maintenance-notes/{id}"""
    err = _require_access(request)
    if err:
        return err

    try:
        note = PortfolioMaintenanceNote.objects.select_related("portfolio").get(pk=note_id)
    except PortfolioMaintenanceNote.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

    if request.method == "DELETE":
        if note.status == "approved":
            return JsonResponse(
                {"error": "Unapprove before deleting"}, status=400
            )
        note.delete()
        return JsonResponse({}, status=200)

    # PUT — discrete-field edit (intro + per-WO ai_summary)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if not isinstance(body, dict):
        return JsonResponse({"error": "Expected JSON object"}, status=400)

    intro = body.get("intro", "")
    edits = body.get("edits", [])

    if not isinstance(edits, list):
        return JsonResponse({"error": "'edits' must be a list"}, status=400)

    VALID_BUCKETS = ("open", "scheduled", "completed", "cancelled")

    snap = copy.deepcopy(note.work_order_snapshot or {})
    for bucket in VALID_BUCKETS:
        snap.setdefault(bucket, [])
    snap["intro"] = intro

    unmatched = []
    for edit in edits:
        bucket = edit.get("bucket", "")
        wo_num = edit.get("work_order_number", "")
        ai_summary = edit.get("ai_summary", "")

        if bucket not in VALID_BUCKETS:
            unmatched.append({"bucket": bucket, "work_order_number": wo_num, "reason": "invalid bucket"})
            continue

        found = False
        for group in snap[bucket]:
            for wo in group.get("work_orders", []):
                if wo.get("work_order_number") == wo_num:
                    wo["ai_summary"] = ai_summary
                    found = True
                    break
            if found:
                break

        if not found:
            unmatched.append({"bucket": bucket, "work_order_number": wo_num, "reason": "not found"})

    if unmatched:
        return JsonResponse(
            {"error": "Unmatched edits — nothing saved", "unmatched": unmatched},
            status=422,
        )

    notes_html = render_maintenance_notes_html_from_snapshot(snap)

    note.work_order_snapshot = snap
    note.notes_html = notes_html
    note.status = "draft"
    note.is_edited = True
    note.save(update_fields=["work_order_snapshot", "notes_html", "status", "is_edited", "updated_at"])

    return JsonResponse(_serialize_maintenance_note(note))


@require_POST
def approve_maintenance_note(request, note_id):
    """POST /api/reports/maintenance-notes/{id}/approve"""
    err = _require_access(request)
    if err:
        return err

    try:
        note = PortfolioMaintenanceNote.objects.select_related("portfolio").get(pk=note_id)
    except PortfolioMaintenanceNote.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

    if not note_has_sendable_content(note):
        return JsonResponse({
            "error": "Cannot approve: this note has no email content. Re-generate this portfolio.",
        }, status=422)

    note.status = "approved"
    note.save(update_fields=["status", "updated_at"])

    return JsonResponse(_serialize_maintenance_note(note))


@require_POST
def generate_maintenance_notes_api(request):
    """POST /api/reports/maintenance-notes/generate — background generation."""
    import comms.maintenance_services as msvc

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

    try:
        period_start = date.fromisoformat(start_str)
        period_end = date.fromisoformat(end_str)
    except ValueError:
        return JsonResponse(
            {"ok": False, "error": "Invalid date format (YYYY-MM-DD)"}, status=400
        )

    is_dry_run = body.get("dry_run", False)

    # Concurrency guard with TTL auto-expiry
    with msvc._maintenance_gen_lock:
        if msvc._maintenance_gen_started_at is not None:
            elapsed = time.monotonic() - msvc._maintenance_gen_started_at
            if elapsed < msvc._MAINTENANCE_GEN_TTL:
                return JsonResponse(
                    {"ok": False, "error": "Generation already in progress"}, status=409
                )
            logger.warning(
                "comms_maintenance_gen_lock_expired",
                extra={"elapsed_seconds": round(elapsed)},
            )
        msvc._maintenance_gen_started_at = time.monotonic()

    portfolios = get_portfolio_generation_scope()

    portfolio_name = body.get("portfolio_name", "").strip()
    if portfolio_name:
        portfolios = portfolios.filter(name__icontains=portfolio_name)

    def _run():
        try:
            generate_portfolio_maintenance_notes(
                portfolios,
                period_start,
                period_end,
                dry_run=is_dry_run,
            )
        except Exception:
            logger.exception("comms_maintenance_gen_background_error")
        finally:
            msvc._maintenance_gen_started_at = None

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return JsonResponse({"ok": True})


@require_GET
def maintenance_generation_progress(request):
    """GET /api/reports/maintenance-notes/progress?week=YYYY-MM-DD
    or  ?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD"""
    import comms.maintenance_services as msvc

    err = _require_access(request)
    if err:
        return err

    resolved = _resolve_period(request)
    if not resolved:
        return JsonResponse({"error": "period params required"}, status=400)

    period_start, period_end, explicit = resolved

    portfolio_name = request.GET.get("portfolio_name", "").strip()

    scope = get_portfolio_generation_scope()
    notes = _notes_for_period(period_start, period_end, explicit)
    if portfolio_name:
        scope = scope.filter(name__icontains=portfolio_name)
        notes = notes.filter(portfolio__name__icontains=portfolio_name)

    total = scope.count()
    generated = notes.count()

    with msvc._maintenance_gen_lock:
        running = (
            msvc._maintenance_gen_started_at is not None
            and (time.monotonic() - msvc._maintenance_gen_started_at) < msvc._MAINTENANCE_GEN_TTL
        )

    return JsonResponse({
        "generated": generated,
        "total": total,
        "running": running,
    })


@require_POST
def approve_all_maintenance_notes(request):
    """POST /api/reports/maintenance-notes/approve-all?week=YYYY-MM-DD"""
    err = _require_access(request)
    if err:
        return err

    period_start = _parse_week(request)
    if not period_start:
        return JsonResponse({"error": "week param required"}, status=400)

    notes = PortfolioMaintenanceNote.objects.filter(
        period_type="weekly",
        period_start=period_start,
        status="draft",
    )

    approved = 0
    skipped_empty = 0
    for note in notes:
        if not note_has_sendable_content(note):
            skipped_empty += 1
            continue
        note.status = "approved"
        note.save(update_fields=["status", "updated_at"])
        approved += 1

    return JsonResponse({"approved": approved, "skipped_empty": skipped_empty})


@require_POST
def delete_all_maintenance_notes(request):
    """POST /api/reports/maintenance-notes/delete-all?week=YYYY-MM-DD"""
    err = _require_access(request)
    if err:
        return err

    period_start = _parse_week(request)
    if not period_start:
        return JsonResponse({"error": "week param required"}, status=400)

    qs = PortfolioMaintenanceNote.objects.filter(
        period_type="weekly",
        period_start=period_start,
        status="draft",
    )

    count = qs.count()
    qs.delete()

    return JsonResponse({"deleted": count})


# ---------------------------------------------------------------------------
# Owner-sends endpoints (Layer 2)
# ---------------------------------------------------------------------------


@require_GET
def list_maintenance_recipients(request):
    """GET /api/reports/maintenance-sends/recipients?week=YYYY-MM-DD"""
    err = _require_access(request)
    if err:
        return err

    period_start = _parse_week(request)
    if not period_start:
        return JsonResponse([], safe=False)

    recipients = get_maintenance_recipients_for_period(period_start, period_type="weekly")
    return JsonResponse(recipients, safe=False)


@require_GET
def preview_maintenance_recipient(request, email):
    """GET /api/reports/maintenance-sends/recipients/{email}/preview?week=YYYY-MM-DD"""
    err = _require_access(request)
    if err:
        return err

    period_start = _parse_week(request)
    if not period_start:
        return JsonResponse({"error": "week param required"}, status=400)

    assembled = assemble_owner_maintenance_email(email, period_start, period_type="weekly")
    if assembled is None:
        return JsonResponse({
            "owner_name": "",
            "body_html": "",
            "portfolios": [],
            "all_approved": False,
        })
    return JsonResponse(assembled)


@require_POST
def send_maintenance_recipient(request, email):
    """POST /api/reports/maintenance-sends/recipients/{email}/send?week=YYYY-MM-DD"""
    err = _require_access(request)
    if err:
        return err

    period_start = _parse_week(request)
    if not period_start:
        return JsonResponse({"error": "week param required"}, status=400)

    period_end = period_start + timedelta(days=6)
    norm_email = _normalize_email(email)

    assembled = assemble_owner_maintenance_email(norm_email, period_start, period_type="weekly")

    if assembled is None:
        return JsonResponse({
            "ok": False,
            "error": "No content to send",
        }, status=400)

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

    if not assembled["body_html"]:
        return JsonResponse({
            "ok": False,
            "error": "No content to send",
        }, status=400)

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

    period_label = _format_period_label("weekly", period_start, period_end)
    subject = f"Weekly Maintenance Update — {period_label}"

    draft, _ = EmailDraft.objects.update_or_create(
        product=PRODUCT,
        recipient_email=norm_email,
        period_type="weekly",
        period_start=period_start,
        defaults={
            "owner": rep_owner,
            "subject": subject,
            "body_html": assembled["body_html"],
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
            "comms_maintenance_send_failed",
            extra={"email": norm_email},
        )
        return JsonResponse(
            {"ok": False, "message": f"Send failed: {exc}"}, status=500
        )


@require_POST
def test_send_maintenance_recipient(request, email):
    """POST /api/reports/maintenance-sends/recipients/{email}/test-send?week=YYYY-MM-DD"""
    err = _require_access(request)
    if err:
        return err

    period_start = _parse_week(request)
    if not period_start:
        return JsonResponse({"error": "week param required"}, status=400)

    period_end = period_start + timedelta(days=6)
    norm_email = _normalize_email(email)

    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        body = {}

    test_to = body.get("to", "").strip() or request.user.email

    assembled = assemble_owner_maintenance_email(norm_email, period_start, period_type="weekly")

    if assembled is None or not assembled["body_html"]:
        return JsonResponse({
            "ok": False,
            "error": "No content to preview",
        }, status=400)

    import sendgrid as sg_module
    from sendgrid.helpers.mail import Mail

    period_label = _format_period_label("weekly", period_start, period_end)
    subject = f"[TEST] Weekly Maintenance Update — {period_label}"

    message = Mail(
        from_email=settings.COMMS_FROM_EMAIL,
        to_emails=test_to,
        subject=subject,
        html_content=assembled["body_html"],
    )

    sg_client = sg_module.SendGridAPIClient(settings.SENDGRID_API_KEY)
    try:
        response = sg_client.send(message)
    except Exception as exc:
        logger.exception(
            "comms_maintenance_test_send_failed",
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
        "comms_maintenance_test_send_ok",
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
def send_all_maintenance_ready(request):
    """POST /api/reports/maintenance-sends/send-all?week=YYYY-MM-DD"""
    err = _require_access(request)
    if err:
        return err

    period_start = _parse_week(request)
    if not period_start:
        return JsonResponse({"error": "week param required"}, status=400)

    period_end = period_start + timedelta(days=6)

    recipients = get_maintenance_recipients_for_period(period_start, period_type="weekly")

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
            assembled = assemble_owner_maintenance_email(
                norm_email, period_start, period_type="weekly"
            )

            if assembled is None or not assembled["body_html"]:
                skipped += 1
                continue

            from core.models import Owner

            rep_owner = Owner.objects.filter(pk=recip["owner_id"]).first()

            if not rep_owner:
                skipped += 1
                continue

            period_label = _format_period_label("weekly", period_start, period_end)
            subject = f"Weekly Maintenance Update — {period_label}"

            draft, _ = EmailDraft.objects.update_or_create(
                product=PRODUCT,
                recipient_email=norm_email,
                period_type="weekly",
                period_start=period_start,
                defaults={
                    "owner": rep_owner,
                    "subject": subject,
                    "body_html": assembled["body_html"],
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
                "comms_maintenance_send_all_failed",
                extra={"email": norm_email},
            )
            failed += 1

    return JsonResponse({"sent": sent, "failed": failed, "skipped": skipped})
