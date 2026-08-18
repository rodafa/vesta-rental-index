"""Server-rendered dashboard views for weekly leasing notes."""

import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from leasing.models import PortfolioLeasingNote
from leasing.note_services import render_leasing_notes_html_from_snapshot

logger = logging.getLogger(__name__)


@require_GET
@login_required
def leasing_notes_list(request):
    """List leasing notes for a period, defaulting to the most recent."""
    if not request.user.can_access("leasing"):
        return HttpResponseForbidden("Access denied.")

    start_param = request.GET.get("start")
    end_param = request.GET.get("end")

    if start_param and end_param:
        from datetime import date

        try:
            period_start = date.fromisoformat(start_param)
            period_end = date.fromisoformat(end_param)
        except ValueError:
            period_start = None
            period_end = None
    else:
        period_start = None
        period_end = None

    # Fall back to most recent note's period
    if period_start is None or period_end is None:
        latest = (
            PortfolioLeasingNote.objects
            .order_by("-period_start")
            .values("period_start", "period_end")
            .first()
        )
        if latest:
            period_start = latest["period_start"]
            period_end = latest["period_end"]

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

            # Distinct owner emails for this portfolio
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

    return render(request, "comms/leasing_notes_list.html", {
        "notes": notes,
        "period_start": period_start,
        "period_end": period_end,
        "total": total,
        "drafts": drafts,
        "approved": approved,
        "edited": edited,
    })


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

    units = []
    for ctx in unit_contexts:
        uid = str(ctx["unit_id"])
        current_text = edited_notes.get(uid) or unit_summaries.get(uid, "")
        units.append({
            "uid": uid,
            "address": ctx.get("address", f"Unit {uid}"),
            "current_text": current_text,
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

    # Build the set of valid unit IDs from the snapshot
    valid_uids = {str(ctx["unit_id"]) for ctx in unit_contexts}

    edited_notes = {}
    for key, value in request.POST.items():
        if not key.startswith("unit_"):
            continue
        uid = key[5:]  # strip "unit_" prefix; already a string
        if uid not in valid_uids:
            logger.warning(
                "leasing_note_edit_invalid_uid",
                extra={"note_id": note_id, "uid": uid},
            )
            continue
        text = value.strip()
        if text:
            edited_notes[uid] = text

    note.edited_notes = edited_notes
    note.notes_html = render_leasing_notes_html_from_snapshot(
        note.unit_snapshot, note.edited_notes,
    )
    note.is_edited = True
    note.save(update_fields=["edited_notes", "notes_html", "is_edited", "updated_at"])

    return redirect("leasing-notes-detail", note_id=note.pk)
