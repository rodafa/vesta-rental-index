"""
Shared logic for AI-drafting PropertyWeeklyNote records.

Used by both the management command and the API endpoint.
"""

import logging
from datetime import date, timedelta

import anthropic
from django.conf import settings

from dashboard.models import PropertyWeeklyNote
from market.models import DailyUnitSnapshot
from market.services.leasing_queries import dls_delta, get_showing_feedback
from reports.services.data_sources.leadsimple import (
    fetch_all_active_deals,
    match_deals_to_address,
)
from weekly_reports.services.voice_guide import load_voice_guide

logger = logging.getLogger(__name__)


def _build_prompt(unit_data):
    """Build the Claude prompt for a single unit."""
    u = unit_data
    voice_guide = load_voice_guide()

    lines = []
    if voice_guide:
        lines += [voice_guide, ""]

    lines += [
        "PROPERTY:",
        f"  Address: {u['address']}",
        f"  Beds/Baths: {u['beds']}BR / {u['baths']}BA",
        f"  Listed at: ${u['list_price']}/mo",
        "",
        "THIS WEEK'S ACTIVITY:",
        f"  Days on market: {u['dom']}",
        f"  New leads: {u['leads']}",
        f"  Showings completed: {u['showings']}",
        f"  Applications received: {u['apps']}",
    ]

    if u.get("feedback_text"):
        lines += [
            "",
            "SHOWING FEEDBACK FROM RENTENGINE:",
            u["feedback_text"][:800],
        ]

    if u.get("active_deals"):
        lines += ["", "ACTIVE APPLICATIONS FROM LEADSIMPLE:"]
        for d in u["active_deals"]:
            lines.append(
                f"- {d['name']} | Stage: {d['stage_name']} | Applied: {d['created_at']}"
            )
            if d.get("comments"):
                lines.append(f"  Notes: {d['comments']}")

    if u.get("history"):
        lines += ["", "RECENT HISTORY (previous weeks, newest first):"]
        for h in u["history"]:
            lines.append(f"  {h['week_date']}: {h['note_text'][:150]}")

    lines += ["", "Write the weekly note now. Do not include a subject line or greeting."]
    return "\n".join(lines)


def _call_claude(prompt):
    """Call Claude Sonnet and return the generated text."""
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def draft_all_property_notes(week_date=None, dry_run=False):
    """Core logic: draft PropertyWeeklyNote for all active units.

    Returns dict: {drafted: N, skipped: N, previews: [...]} (previews populated in dry-run mode).
    """
    from properties.models import Unit

    today = date.today()
    if week_date is None:
        week_date = today - timedelta(days=today.weekday())

    week_start = week_date
    week_end = week_date + timedelta(days=6)

    # 1. Find all actively listed units (latest snapshot, status=active)
    latest_date = (
        DailyUnitSnapshot.objects.order_by("-snapshot_date")
        .values_list("snapshot_date", flat=True)
        .first()
    )
    if not latest_date:
        logger.warning("draft_property_notes: no snapshots found")
        return {"drafted": 0, "skipped": 0, "previews": []}

    snapshots = {
        s["unit_id"]: s
        for s in DailyUnitSnapshot.objects.filter(
            snapshot_date=latest_date, status="active"
        ).values("unit_id", "days_on_market", "listed_price")
    }

    if not snapshots:
        return {"drafted": 0, "skipped": 0, "previews": []}

    unit_ids = list(snapshots.keys())

    # 2. Load units with their related property for address matching
    units = list(
        Unit.objects.filter(id__in=unit_ids)
        .select_related("property")
        .only("id", "name", "bedrooms", "full_bathrooms", "half_bathrooms", "address_line_1", "property__address_line_1")
    )

    # 3. Weekly activity via shared dls_delta
    weekly = dls_delta(unit_ids, week_start, week_end)

    # 4. Fetch LeadSimple active deals ONCE
    deals = fetch_all_active_deals()

    # 5. Fetch history (last 3 notes per unit) in bulk
    history_map = {}
    for note in PropertyWeeklyNote.objects.filter(
        unit_id__in=unit_ids,
        week_date__lt=week_start,
    ).order_by("unit_id", "-week_date"):
        bucket = history_map.setdefault(note.unit_id, [])
        if len(bucket) < 3:
            bucket.append({
                "week_date": note.week_date.isoformat(),
                "note_text": note.note_text,
            })

    # 6. Process each unit
    drafted = 0
    skipped = 0
    previews = []

    for unit in units:
        snap = snapshots.get(unit.id, {})
        dom = snap.get("days_on_market") or 0
        list_price = snap.get("listed_price") or 0
        beds = unit.bedrooms or 0
        baths = (unit.full_bathrooms or 0) + (unit.half_bathrooms or 0) * 0.5

        address = (
            unit.address_line_1
            or (unit.property.address_line_1 if unit.property else "")
            or f"Unit #{unit.id}"
        )

        wk = weekly.get(unit.id, {"leads": 0, "showings": 0, "apps": 0})

        # Showing feedback via shared service
        feedback_texts = get_showing_feedback(unit.id, week_start, week_end)
        feedback_text = "\n".join(feedback_texts) if feedback_texts else ""

        # LeadSimple deal matching via shared data_sources module
        prop_address = unit.property.address_line_1 if unit.property else address
        active_deals = match_deals_to_address(prop_address, deals)

        # History
        history = history_map.get(unit.id, [])

        unit_data = {
            "unit_id": unit.id,
            "address": address,
            "beds": beds,
            "baths": baths,
            "list_price": int(list_price) if list_price else 0,
            "dom": dom,
            "leads": wk["leads"],
            "showings": wk["showings"],
            "apps": wk["apps"],
            "feedback_text": feedback_text,
            "active_deals": active_deals,
            "history": history,
        }

        prompt = _build_prompt(unit_data)

        if dry_run:
            # Build prompt only; use a placeholder draft
            try:
                draft_text = _call_claude(prompt)
            except Exception:
                logger.exception("draft_property_notes: Claude call failed for unit %s", unit.id)
                draft_text = "[Claude call failed]"
            previews.append({
                "unit_id": unit.id,
                "address": address,
                "draft": draft_text,
            })
            drafted += 1
            continue

        # Real run — call Claude and save
        try:
            draft_text = _call_claude(prompt)
        except Exception:
            logger.exception("draft_property_notes: Claude call failed for unit %s", unit.id)
            skipped += 1
            continue

        PropertyWeeklyNote.objects.update_or_create(
            unit_id=unit.id,
            week_date=week_start,
            defaults={
                "note_text": draft_text,
                "author": "AI",
                "approved": False,
                "approved_at": None,
                "approved_by": "",
            },
        )
        drafted += 1
        logger.info("Drafted note for unit %s (%s)", unit.id, address)

    return {"drafted": drafted, "skipped": skipped, "previews": previews}
