"""Server-rendered dashboard views for weekly leasing notes."""

import logging
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from comms.models import EmailDraft
from comms.services import send_draft
from leasing.email_services import assemble_leasing_drafts
from leasing.models import PortfolioLeasingNote
from leasing.note_services import render_leasing_notes_html_from_snapshot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _period_params(request):
    """Extract and return (period_start, period_end) from GET params, or (None, None)."""
    from datetime import date

    start_param = request.GET.get("start")
    end_param = request.GET.get("end")
    if start_param and end_param:
        try:
            return date.fromisoformat(start_param), date.fromisoformat(end_param)
        except ValueError:
            pass
    return None, None


def _default_period():
    """Return the most recent note period, or (None, None)."""
    latest = (
        PortfolioLeasingNote.objects
        .order_by("-period_start")
        .values("period_start", "period_end")
        .first()
    )
    if latest:
        return latest["period_start"], latest["period_end"]
    return None, None


def _period_qs(period_start, period_end):
    """Return query string for period params."""
    if period_start and period_end:
        return urlencode({"start": period_start.isoformat(), "end": period_end.isoformat()})
    return ""


def _check_draft_staleness(drafts, period_start, period_type="weekly"):
    """
    For each draft, check whether any contributing PortfolioLeasingNote
    was updated after the draft was assembled.

    Returns a dict mapping draft.pk -> bool (True = stale).
    """
    stale_map = {}
    for draft in drafts:
        portfolio_pks = list(draft.owner.portfolios.values_list("pk", flat=True))
        max_note_updated = PortfolioLeasingNote.objects.filter(
            portfolio_id__in=portfolio_pks,
            period_type=period_type,
            period_start=period_start,
        ).aggregate(latest=Max("updated_at"))["latest"]

        stale_map[draft.pk] = bool(
            max_note_updated
            and draft.generated_at
            and max_note_updated > draft.generated_at
        )
    return stale_map


# ---------------------------------------------------------------------------
# Notes list
# ---------------------------------------------------------------------------

@require_GET
@login_required
def leasing_notes_list(request):
    """List leasing notes for a period, defaulting to the most recent."""
    if not request.user.can_access("leasing"):
        return HttpResponseForbidden("Access denied.")

    period_start, period_end = _period_params(request)
    if period_start is None or period_end is None:
        period_start, period_end = _default_period()

    notes = []
    total = drafts = approved = edited = 0

    if period_start and period_end:
        qs = (
            PortfolioLeasingNote.objects
            .filter(period_start=period_start, period_end=period_end)
            .select_related("portfolio")
            .order_by("portfolio__name")
        )
        for note in qs:
            snapshot = note.unit_snapshot or {}
            unit_count = len(snapshot.get("unit_contexts", []))

            owner_emails = set()
            for owner in note.portfolio.owners.all():
                email = (owner.email or "").strip().lower()
                if email:
                    owner_emails.add(email)

            notes.append({
                "note": note,
                "unit_count": unit_count,
                "recipient_count": len(owner_emails),
            })

            total += 1
            if note.status == "draft":
                drafts += 1
            elif note.status == "approved":
                approved += 1
            if note.is_edited:
                edited += 1

    # Check for drafts ready count
    draft_count = 0
    if period_start and period_end:
        draft_count = EmailDraft.objects.filter(
            product="leasing",
            period_start=period_start,
            period_end=period_end,
        ).count()

    return render(request, "comms/leasing_notes_list.html", {
        "notes": notes,
        "period_start": period_start,
        "period_end": period_end,
        "total": total,
        "drafts": drafts,
        "approved": approved,
        "edited": edited,
        "all_approved": total > 0 and drafts == 0,
        "draft_count": draft_count,
        "error_message": request.GET.get("error", ""),
        "success_message": request.GET.get("success", ""),
    })


# ---------------------------------------------------------------------------
# Note detail / edit
# ---------------------------------------------------------------------------

@require_http_methods(["GET", "POST"])
@login_required
def leasing_note_detail_or_edit(request, note_id):
    """GET: detail/preview. POST: save edits."""
    if not request.user.can_access("leasing"):
        return HttpResponseForbidden("Access denied.")

    if request.method == "POST":
        return _leasing_note_edit(request, note_id)
    return _leasing_note_detail(request, note_id)


def _leasing_note_detail(request, note_id):
    """Render detail page with email preview and per-unit edit form."""
    note = get_object_or_404(PortfolioLeasingNote, pk=note_id)
    snapshot = note.unit_snapshot or {}
    unit_contexts = snapshot.get("unit_contexts", [])
    unit_summaries = (snapshot.get("ai_result") or {}).get("unit_summaries", {})
    edited_notes = note.edited_notes or {}
    recommended_actions = note.recommended_actions or {}

    units = []
    for ctx in unit_contexts:
        uid = str(ctx["unit_id"])
        current_text = edited_notes.get(uid) or unit_summaries.get(uid, "")
        current_action = recommended_actions.get(uid, "")
        units.append({
            "uid": uid,
            "address": ctx.get("address", f"Unit {uid}"),
            "current_text": current_text,
            "current_action": current_action,
        })

    period_label = ""
    if note.period_start and note.period_end:
        period_label = f"{note.period_start.strftime('%b %d')} \u2013 {note.period_end.strftime('%b %d, %Y')}"

    return render(request, "comms/leasing_note_detail.html", {
        "note": note,
        "period_label": period_label,
        "units": units,
        "has_snapshot": bool(unit_contexts),
        "notes_html": note.notes_html,
        "period_start": note.period_start,
        "period_end": note.period_end,
    })


def _leasing_note_edit(request, note_id):
    """Process POST: save per-unit edited text, re-render HTML."""
    note = get_object_or_404(PortfolioLeasingNote, pk=note_id)
    snapshot = note.unit_snapshot or {}
    unit_contexts = snapshot.get("unit_contexts", [])

    valid_uids = {str(ctx["unit_id"]) for ctx in unit_contexts}

    edited_notes = {}
    recommended_actions = {}
    for key, value in request.POST.items():
        if key.startswith("unit_"):
            uid = key[5:]
            if uid not in valid_uids:
                logger.warning(
                    "leasing_note_edit_invalid_uid",
                    extra={"note_id": note_id, "uid": uid},
                )
                continue
            text = value.strip()
            if text:
                edited_notes[uid] = text
        elif key.startswith("action_"):
            uid = key[7:]
            if uid not in valid_uids:
                logger.warning(
                    "leasing_note_edit_invalid_action_uid",
                    extra={"note_id": note_id, "uid": uid},
                )
                continue
            text = value.strip()
            if text:
                recommended_actions[uid] = text

    note.edited_notes = edited_notes
    note.recommended_actions = recommended_actions
    note.notes_html = render_leasing_notes_html_from_snapshot(
        note.unit_snapshot, note.edited_notes, note.recommended_actions,
    )
    note.is_edited = True
    note.save(update_fields=[
        "edited_notes", "recommended_actions", "notes_html",
        "is_edited", "updated_at",
    ])

    return redirect("leasing-notes-detail", note_id=note.pk)


# ---------------------------------------------------------------------------
# Approve / unapprove
# ---------------------------------------------------------------------------

@require_POST
@login_required
def leasing_note_approve(request, note_id):
    """Flip PortfolioLeasingNote.status between draft and approved."""
    if not request.user.can_access("leasing"):
        return HttpResponseForbidden("Access denied.")

    note = get_object_or_404(PortfolioLeasingNote, pk=note_id)
    action = request.POST.get("action", "")

    if action == "approve":
        note.status = "approved"
    elif action == "unapprove":
        note.status = "draft"
    else:
        return redirect("leasing-notes-detail", note_id=note.pk)

    note.save(update_fields=["status", "updated_at"])

    logger.info(
        "leasing_note_status_changed",
        extra={
            "note_id": note.pk,
            "portfolio": str(note.portfolio),
            "new_status": note.status,
            "user": request.user.username,
        },
    )

    qs = _period_qs(note.period_start, note.period_end)
    url = f"/dashboard/leasing-notes/{note.pk}/"
    if qs:
        url += f"?{qs}"
    return redirect(url)


# ---------------------------------------------------------------------------
# Assemble drafts
# ---------------------------------------------------------------------------

@require_POST
@login_required
def leasing_notes_assemble(request):
    """Assemble EmailDraft rows for all approved leasing notes in the period."""
    if not request.user.can_access("leasing"):
        return HttpResponseForbidden("Access denied.")

    period_start, period_end = _period_params(request)
    if period_start is None or period_end is None:
        period_start, period_end = _default_period()

    if not period_start or not period_end:
        qs = _period_qs(period_start, period_end)
        return redirect(f"/dashboard/leasing-notes/?{urlencode({'error': 'No period selected.'})}&{qs}")

    result = assemble_leasing_drafts(period_start, period_end)

    qs_params = {"start": period_start.isoformat(), "end": period_end.isoformat()}

    if result["blocking_portfolios"]:
        names = ", ".join(result["blocking_portfolios"])
        qs_params["error"] = f"Cannot assemble: not approved: {names}"
        return redirect(f"/dashboard/leasing-notes/?{urlencode(qs_params)}")

    qs_params["success"] = (
        f"Assembled: {result['created']} created, {result['updated']} updated, "
        f"{result['skipped']} skipped."
    )
    if result["errors"]:
        qs_params["success"] += f" {len(result['errors'])} error(s)."

    return redirect(f"/dashboard/leasing-notes/drafts/?{urlencode(qs_params)}")


# ---------------------------------------------------------------------------
# Drafts list
# ---------------------------------------------------------------------------

@require_GET
@login_required
def leasing_drafts_list(request):
    """List EmailDraft rows for the period with staleness detection."""
    if not request.user.can_access("leasing"):
        return HttpResponseForbidden("Access denied.")

    period_start, period_end = _period_params(request)
    if period_start is None or period_end is None:
        period_start, period_end = _default_period()

    drafts_qs = []
    stale_map = {}
    has_stale = False

    if period_start and period_end:
        drafts_qs = list(
            EmailDraft.objects.filter(
                product="leasing",
                period_start=period_start,
                period_end=period_end,
            )
            .select_related("owner")
            .order_by("recipient_email")
        )

        stale_map = _check_draft_staleness(drafts_qs, period_start)
        has_stale = any(stale_map.values())

    # Build display rows with unit/portfolio counts
    draft_rows = []
    unsent_rows = []
    for draft in drafts_qs:
        portfolio_pks = list(draft.owner.portfolios.values_list("pk", flat=True))
        notes = PortfolioLeasingNote.objects.filter(
            portfolio_id__in=portfolio_pks,
            period_type="weekly",
            period_start=period_start,
        )
        portfolio_count = notes.count()
        unit_count = 0
        for n in notes:
            snap = n.unit_snapshot or {}
            unit_count += len(snap.get("unit_contexts", []))

        is_stale = stale_map.get(draft.pk, False)
        row = {
            "draft": draft,
            "portfolio_count": portfolio_count,
            "unit_count": unit_count,
            "is_stale": is_stale,
        }
        draft_rows.append(row)
        if draft.status != "sent" and not is_stale:
            unsent_rows.append(row)

    return render(request, "comms/leasing_drafts_list.html", {
        "draft_rows": draft_rows,
        "unsent_rows": unsent_rows,
        "period_start": period_start,
        "period_end": period_end,
        "has_stale": has_stale,
        "success_message": request.GET.get("success", ""),
        "error_message": request.GET.get("error", ""),
    })


# ---------------------------------------------------------------------------
# Test send
# ---------------------------------------------------------------------------

@require_POST
@login_required
def leasing_draft_test_send(request, draft_id):
    """Send a draft's body_html to request.user.email with [TEST] prefix."""
    if not request.user.can_access("leasing"):
        return HttpResponseForbidden("Access denied.")

    draft = get_object_or_404(EmailDraft, pk=draft_id, product="leasing")

    period_start = draft.period_start
    period_end = draft.period_end
    qs_params = {"start": period_start.isoformat(), "end": period_end.isoformat()}

    user_email = (request.user.email or "").strip()
    if not user_email:
        qs_params["error"] = "Cannot test-send: your account has no email address."
        return redirect(f"/dashboard/leasing-notes/drafts/?{urlencode(qs_params)}")

    try:
        # Temporarily prefix subject for test
        original_subject = draft.subject
        draft.subject = f"[TEST] {original_subject}"
        send_draft(draft, request.user, recipient_override=user_email)
        draft.subject = original_subject  # restore in memory (not saved — send_draft doesn't save subject)

        qs_params["success"] = f"Test email sent to {user_email}."
    except Exception as exc:
        qs_params["error"] = f"Test send failed: {exc}"
        logger.exception(
            "leasing_test_send_failed",
            extra={"draft_id": draft_id, "user": request.user.username},
        )

    return redirect(f"/dashboard/leasing-notes/drafts/?{urlencode(qs_params)}")


# ---------------------------------------------------------------------------
# Live send — typed confirmation + recipient selection
# ---------------------------------------------------------------------------

@require_POST
@login_required
def leasing_drafts_send(request):
    """
    Send selected leasing drafts. Requires POST field confirm="SEND".

    Safety:
    - Server-side typed confirmation gate
    - Re-reads each draft from DB before sending (no stale in-memory state)
    - Skips already-sent drafts
    - Skips stale drafts (note updated after assembly)
    - Refuses drafts whose notes are not all approved
    - Each send wrapped in try/except — one failure doesn't abort the rest
    """
    if not request.user.can_access("leasing"):
        return HttpResponseForbidden("Access denied.")

    period_start, period_end = _period_params(request)
    if period_start is None or period_end is None:
        period_start, period_end = _default_period()

    qs_params = {}
    if period_start and period_end:
        qs_params = {"start": period_start.isoformat(), "end": period_end.isoformat()}

    # Typed confirmation gate
    confirm = (request.POST.get("confirm") or "").strip()
    if confirm != "SEND":
        qs_params["error"] = "Send refused: type SEND to confirm."
        return redirect(f"/dashboard/leasing-notes/drafts/?{urlencode(qs_params)}")

    # Collect selected draft IDs from checkboxes
    selected_ids = set()
    for key in request.POST:
        if key.startswith("draft_"):
            try:
                selected_ids.add(int(key[6:]))
            except ValueError:
                pass

    if not selected_ids:
        qs_params["error"] = "No recipients selected."
        return redirect(f"/dashboard/leasing-notes/drafts/?{urlencode(qs_params)}")

    sent = 0
    skipped_sent = 0
    skipped_stale = 0
    skipped_unapproved = 0
    failed = 0
    errors = []

    for draft_id in sorted(selected_ids):
        # Re-read from DB each iteration
        try:
            draft = EmailDraft.objects.select_related("owner").get(
                pk=draft_id, product="leasing",
            )
        except EmailDraft.DoesNotExist:
            errors.append(f"Draft {draft_id}: not found")
            failed += 1
            continue

        # Skip already sent
        if draft.status == "sent":
            skipped_sent += 1
            logger.info(
                "leasing_send_skipped_already_sent",
                extra={"draft_id": draft.pk, "recipient": draft.recipient_email},
            )
            continue

        # Check staleness
        stale_map = _check_draft_staleness([draft], draft.period_start)
        if stale_map.get(draft.pk, False):
            skipped_stale += 1
            errors.append(f"{draft.recipient_email}: stale — reassemble first")
            logger.warning(
                "leasing_send_skipped_stale",
                extra={"draft_id": draft.pk, "recipient": draft.recipient_email},
            )
            continue

        # Check all contributing notes are approved
        portfolio_pks = list(draft.owner.portfolios.values_list("pk", flat=True))
        unapproved = list(
            PortfolioLeasingNote.objects.filter(
                portfolio_id__in=portfolio_pks,
                period_type=draft.period_type,
                period_start=draft.period_start,
            ).exclude(status="approved").values_list("portfolio__name", flat=True)
        )
        if unapproved:
            skipped_unapproved += 1
            names = ", ".join(unapproved)
            errors.append(f"{draft.recipient_email}: unapproved notes ({names})")
            logger.warning(
                "leasing_send_skipped_unapproved",
                extra={
                    "draft_id": draft.pk,
                    "recipient": draft.recipient_email,
                    "unapproved_portfolios": unapproved,
                },
            )
            continue

        # Send
        try:
            send_draft(draft, request.user)
            sent += 1
            logger.info(
                "leasing_send_ok",
                extra={
                    "draft_id": draft.pk,
                    "recipient": draft.recipient_email,
                    "sent_by": request.user.username,
                },
            )
        except Exception as exc:
            failed += 1
            errors.append(f"{draft.recipient_email}: {exc}")
            logger.exception(
                "leasing_send_failed",
                extra={
                    "draft_id": draft.pk,
                    "recipient": draft.recipient_email,
                    "sent_by": request.user.username,
                },
            )

    parts = [f"{sent} sent"]
    if skipped_sent:
        parts.append(f"{skipped_sent} already sent")
    if skipped_stale:
        parts.append(f"{skipped_stale} stale")
    if skipped_unapproved:
        parts.append(f"{skipped_unapproved} unapproved")
    if failed:
        parts.append(f"{failed} failed")

    qs_params["success"] = "Send complete: " + ", ".join(parts) + "."
    if errors:
        qs_params["error"] = " | ".join(errors)

    return redirect(f"/dashboard/leasing-notes/drafts/?{urlencode(qs_params)}")
