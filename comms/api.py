"""
Monthly owner notes API — serves the dashboard JS (monthly_notes.js).

All endpoints are session-authenticated and role-gated to monthly_owner_notes.
Backed directly by EmailDraft — no new model.
"""

import json
import logging
import threading

from django.http import JsonResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .models import EmailDraft
from .services import generate_drafts, generate_monthly_notes, send_draft, _format_period_label
from core.models import Owner

logger = logging.getLogger(__name__)

PRODUCT = "monthly_owner_notes"


def _require_access(request):
    """Check login and role access. Returns JsonResponse on failure, None on success."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)
    if not request.user.can_access(PRODUCT):
        return JsonResponse({"error": "Forbidden"}, status=403)
    return None


def _serialize_draft(draft):
    """Serialize an EmailDraft to the note object shape the JS expects."""
    # Derive portfolio_name from owner's portfolios (all, comma-separated)
    portfolios = list(draft.owner.portfolios.order_by("name").values_list("name", flat=True))
    portfolio_name = ", ".join(portfolios) if portfolios else "\u2014"

    note_text = draft.generated_note or ""
    word_count = len(note_text.split()) if note_text.strip() else 0

    # Map internal status to JS status vocabulary
    status_map = {"draft": "pending", "approved": "approved", "sent": "sent"}
    js_status = status_map.get(draft.status, draft.status)

    # Financial fields from latest posted PortfolioStatement
    statement_period = None
    total_income = None
    total_expenses = None
    total_distribution = None
    ending_balance = None

    from accounting.models import PortfolioStatement

    portfolio_pks = draft.owner.portfolios.values_list("pk", flat=True)
    latest_stmt = (
        PortfolioStatement.objects.filter(
            portfolio_id__in=portfolio_pks, status="2"
        )
        .order_by("-period_end")
        .first()
    )
    if latest_stmt:
        statement_period = (
            f"{latest_stmt.period_start.strftime('%b %d')} – "
            f"{latest_stmt.period_end.strftime('%b %d, %Y')}"
        )
        total_income = float(latest_stmt.total_income)
        total_expenses = float(latest_stmt.total_expenses)
        total_distribution = float(latest_stmt.total_distribution)
        ending_balance = float(latest_stmt.ending_balance)

    return {
        "id": draft.pk,
        "portfolio_name": portfolio_name,
        "owner_name": draft.owner.name,
        "recipient_email": draft.recipient_email,
        "status": js_status,
        "word_count": word_count,
        "generated_note": note_text,
        "error_message": None,
        "statement_period": statement_period,
        "total_income": total_income,
        "total_expenses": total_expenses,
        "total_distribution": total_distribution,
        "ending_balance": ending_balance,
    }


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


def _get_draft_or_404(draft_id):
    """Get a monthly_owner_notes draft by ID. Returns (draft, None) or (None, JsonResponse)."""
    try:
        draft = EmailDraft.objects.select_related("owner").get(
            pk=draft_id, product=PRODUCT
        )
        return draft, None
    except EmailDraft.DoesNotExist:
        return None, JsonResponse({"error": "Not found"}, status=404)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@require_GET
def list_notes(request):
    """GET /api/reports/owner-notes?month=YYYY-MM"""
    err = _require_access(request)
    if err:
        return err

    parsed = _parse_month(request)
    if not parsed:
        return JsonResponse([], safe=False)

    year, month = parsed
    drafts = (
        EmailDraft.objects.filter(
            product=PRODUCT,
            period_start__year=year,
            period_start__month=month,
        )
        .select_related("owner")
        .prefetch_related("owner__portfolios")
        .order_by("owner__name")
    )

    return JsonResponse([_serialize_draft(d) for d in drafts], safe=False)


@require_http_methods(["PUT", "DELETE"])
def note_detail(request, draft_id):
    """PUT or DELETE /api/reports/owner-notes/{id}"""
    err = _require_access(request)
    if err:
        return err

    draft, err = _get_draft_or_404(draft_id)
    if err:
        return err

    if request.method == "DELETE":
        if draft.status == "sent":
            return JsonResponse(
                {"error": "Cannot delete a sent draft"}, status=403
            )
        draft.delete()
        return JsonResponse({}, status=200)

    # PUT — update generated_note, re-render body_html
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    new_note = body.get("generated_note", "")
    draft.generated_note = new_note

    # Re-render body_html from the edited plain text
    period_label = _format_period_label(
        draft.period_type, draft.period_start, draft.period_end
    )
    draft.body_html = render_to_string(
        "comms/emails/monthly_owner_notes_edited.html",
        {
            "note_text": new_note,
            "period_label": period_label,
            "owner_first_name": draft.owner.first_name or draft.owner.name,
        },
    )

    draft.save(update_fields=["generated_note", "body_html"])

    return JsonResponse(_serialize_draft(draft))


@require_POST
def approve_note(request, draft_id):
    """POST /api/reports/owner-notes/{id}/approve"""
    err = _require_access(request)
    if err:
        return err

    draft, err = _get_draft_or_404(draft_id)
    if err:
        return err

    if draft.status == "sent":
        return JsonResponse({"error": "Cannot approve a sent draft"}, status=400)

    draft.status = "approved"
    draft.save(update_fields=["status"])

    return JsonResponse(_serialize_draft(draft))


@require_POST
def send_note(request, draft_id):
    """POST /api/reports/owner-notes/{id}/send"""
    err = _require_access(request)
    if err:
        return err

    draft, err = _get_draft_or_404(draft_id)
    if err:
        return err

    try:
        result = send_draft(draft, request.user)
        response_data = _serialize_draft(draft)
        response_data["ok"] = True
        response_data["message"] = "Email sent."
        return JsonResponse(response_data)
    except (PermissionError, ValueError) as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=400)
    except Exception as exc:
        logger.exception("comms_api_send_failed", extra={"draft_id": draft_id})
        return JsonResponse(
            {"ok": False, "message": f"Send failed: {exc}"}, status=500
        )


@require_POST
def generate_notes(request):
    """POST /api/reports/owner-notes/generate — trigger background generation."""
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

    owners = Owner.objects.filter(
        is_active=True,
    ).exclude(
        email=""
    ).exclude(
        email__isnull=True
    ).prefetch_related("portfolios")

    owner_id = body.get("owner_id")
    if owner_id:
        try:
            owners = owners.filter(pk=int(owner_id))
        except (ValueError, TypeError):
            pass

    def _run():
        try:
            generate_monthly_notes(
                owners,
                period_start,
                period_end,
                dry_run=is_dry_run,
            )
        except Exception:
            logger.exception("comms_api_generate_background_error")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return JsonResponse({"ok": True})


@require_POST
def send_all(request):
    """POST /api/reports/owner-notes/send-all?month=YYYY-MM"""
    err = _require_access(request)
    if err:
        return err

    parsed = _parse_month(request)
    if not parsed:
        return JsonResponse({"error": "month param required"}, status=400)

    year, month = parsed
    drafts = EmailDraft.objects.filter(
        product=PRODUCT,
        status="approved",
        period_start__year=year,
        period_start__month=month,
    ).select_related("owner")

    sent = 0
    failed = 0
    for draft in drafts:
        try:
            send_draft(draft, request.user)
            sent += 1
        except Exception:
            logger.exception(
                "comms_api_send_all_failed", extra={"draft_id": draft.pk}
            )
            failed += 1

    return JsonResponse({"sent": sent, "failed": failed})


@require_POST
def approve_all(request):
    """POST /api/reports/owner-notes/approve-all?month=YYYY-MM"""
    err = _require_access(request)
    if err:
        return err

    parsed = _parse_month(request)
    if not parsed:
        return JsonResponse({"error": "month param required"}, status=400)

    year, month = parsed
    count = EmailDraft.objects.filter(
        product=PRODUCT,
        status="draft",
        period_start__year=year,
        period_start__month=month,
    ).update(status="approved")

    return JsonResponse({"approved": count})


@require_POST
def delete_all(request):
    """POST /api/reports/owner-notes/delete-all?month=YYYY-MM"""
    err = _require_access(request)
    if err:
        return err

    parsed = _parse_month(request)
    if not parsed:
        return JsonResponse({"error": "month param required"}, status=400)

    year, month = parsed
    qs = EmailDraft.objects.filter(
        product=PRODUCT,
        period_start__year=year,
        period_start__month=month,
    ).exclude(status="sent")

    count = qs.count()
    qs.delete()

    return JsonResponse({"deleted": count})
