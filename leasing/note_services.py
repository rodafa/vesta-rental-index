"""
Leasing note generation (Layer 1: PortfolioLeasingNote).

Pure data assembly + single Anthropic call per portfolio.
No email sending, no dashboard, no envelope — those are Layer 2.
"""

import logging
import re
import unicodedata
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.template.loader import render_to_string
from django.utils import timezone as django_tz

from comms.services import _call_anthropic, _get_or_create_voice_guide

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


def _safe_delta(current, prior):
    """Compute current - prior, returning None if either side is None."""
    if current is None or prior is None:
        return None
    return current - prior


def _clean_feedback_text(text):
    """
    Strip RentEngine form labels and control characters from feedback.

    - Remove a leading line ending in a colon when followed by a newline
    - Remove U+FFFC and other non-printable characters
    - Collapse internal newlines to a single space
    - Strip leading/trailing whitespace
    """
    # Remove leading label line (e.g. "Things the prospect liked:\n")
    text = re.sub(r"^[^\n]*:\n", "", text)
    # Remove U+FFFC and any non-printable characters (keep whitespace)
    text = "".join(
        ch for ch in text
        if ch in ("\n", "\t", " ") or (not unicodedata.category(ch).startswith("C"))
    )
    # Collapse newlines to single space
    text = re.sub(r"\n+", " ", text)
    return text.strip()


def _dedup_feedback(raw_feedback):
    """
    Deduplicate showing feedback on (prospect_id, date of created_at),
    keeping the longest feedback text. Drops entries with null feedback.

    Returns only feedback text and date — prospect names are stripped
    so they can never leak into the AI prompt.
    """
    if not raw_feedback:
        return []

    best = {}  # (prospect_id, date_str) -> {"feedback": str, "date": str}
    for entry in raw_feedback:
        feedback_text = entry.get("feedback")
        if feedback_text is None:
            continue
        feedback_text = _clean_feedback_text(feedback_text)
        if not feedback_text:
            continue
        prospect_id = entry.get("prospect_id")
        created_at = str(entry.get("created_at", ""))
        date_key = created_at[:10]
        key = (prospect_id, date_key)

        existing = best.get(key)
        if existing is None or len(feedback_text) > len(existing["feedback"]):
            best[key] = {"feedback": feedback_text, "date": date_key}

    return list(best.values())


def _ordinal(n):
    """Return day-of-month with ordinal suffix: 1st, 2nd, 3rd, 4th..."""
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]}"


def _format_tour_time(dt):
    """Format a UTC datetime as e.g. 'Monday, July 13th at 2:00pm' in Eastern time."""
    eastern = dt.astimezone(ET)
    hour = eastern.strftime("%I").lstrip("0") or "12"
    minute = eastern.strftime("%M")
    ampm = eastern.strftime("%p").lower()
    day_name = eastern.strftime("%A")
    month_name = eastern.strftime("%B")
    day = eastern.day
    return f"{day_name}, {month_name} {_ordinal(day)} at {hour}:{minute}{ampm}"


def _format_val(val, delta=None):
    """Format a metric value with optional delta for the prompt."""
    if val is None:
        return "unknown"
    s = str(val)
    if delta is not None and delta != 0:
        s += f" ({delta:+d} vs prior week)"
    return s


def build_unit_context(unit, snapshot, prior_snapshot, period_end):
    """
    Build structured facts for one unit's leasing note.

    Pure function except for the LeasingEvent query for upcoming tours.

    When prior_snapshot is None, every delta is None — not zero.
    A null snapshot value passes through as None — never coerced to 0.

    Upcoming tours are relative to the reporting period (period_end),
    not wallclock time, so regenerating an old period never picks up
    future tours.
    """
    from leasing.models import LeasingEvent

    # --- Core fields ---
    address = unit.display_address
    advertised_rent = unit.rentengine_advertised_rent
    rentengine_status = unit.rentengine_status or ""
    days_on_market = snapshot.days_on_market  # nullable

    # --- Six event metrics (always populated, default 0) ---
    metrics = {
        "new_leads": snapshot.new_leads,
        "showings_scheduled": snapshot.showings_scheduled,
        "showings_completed": snapshot.showings_completed,
        "showings_canceled": snapshot.showings_canceled,
        "showings_missed": snapshot.showings_missed,
        "applications_received": snapshot.applications_received,
    }

    # --- Deltas: None when no prior, never zero-as-default ---
    if prior_snapshot is None:
        deltas = {f"{k}_delta": None for k in metrics}
    else:
        deltas = {
            f"{k}_delta": metrics[k] - getattr(prior_snapshot, k)
            for k in metrics
        }

    # --- Nullable reporting fields + deltas ---
    touchpoints_calls = snapshot.touchpoints_calls
    touchpoints_texts = snapshot.touchpoints_texts
    upcoming_showings = snapshot.upcoming_showings

    touchpoints_calls_delta = _safe_delta(
        touchpoints_calls,
        prior_snapshot.touchpoints_calls if prior_snapshot else None,
    )
    touchpoints_texts_delta = _safe_delta(
        touchpoints_texts,
        prior_snapshot.touchpoints_texts if prior_snapshot else None,
    )
    upcoming_showings_delta = _safe_delta(
        upcoming_showings,
        prior_snapshot.upcoming_showings if prior_snapshot else None,
    )
    days_on_market_delta = _safe_delta(
        days_on_market,
        prior_snapshot.days_on_market if prior_snapshot else None,
    )

    # --- Derived values (pre-computed so the LLM never does arithmetic) ---
    period_days = (snapshot.period_end - snapshot.period_start).days + 1
    leads_per_day = round(snapshot.new_leads / period_days, 2) if period_days > 0 else 0.0
    price_review_suggested = leads_per_day < 0.5
    dom_urgent = True if (days_on_market is not None and days_on_market >= 35) else None

    # --- Showing feedback (deduplicated, names stripped) ---
    raw_feedback = (snapshot.reporting_raw or {}).get("showing_feedback", [])
    feedback = _dedup_feedback(raw_feedback)

    # --- Upcoming tours (relative to reporting period, not wallclock) ---
    # Collect Showing Scheduled events in the 14-day window after the period.
    # Then exclude any tour whose prospect later had a "Showing Canceled" or
    # "Missed Showing" event for the same unit (by event_timestamp, not
    # planned_date_time). A scheduled-then-canceled tour is not upcoming —
    # listing it would put a false statement in front of an owner.
    window_start = django_tz.make_aware(
        datetime.combine(period_end, time.max), ET
    )
    window_end = window_start + timedelta(days=14)
    upcoming_tours = []
    tour_events = list(
        LeasingEvent.objects
        .filter(
            unit=unit,
            event_type="Showing Scheduled",
            planned_date_time__isnull=False,
            planned_date_time__gt=window_start,
            planned_date_time__lte=window_end,
        )
        .order_by("planned_date_time")
    )
    if tour_events:
        # Build set of prospect IDs that have a cancellation or miss AFTER
        # their scheduled event, meaning the tour is no longer happening.
        scheduled_prospect_ids = {
            e.prospect_id for e in tour_events if e.prospect_id is not None
        }
        cancel_events = (
            LeasingEvent.objects
            .filter(
                unit=unit,
                event_type__in=["Showing Canceled", "Missed Showing"],
                prospect_id__in=scheduled_prospect_ids,
            )
            .values_list("prospect_id", "event_timestamp")
        )
        # Map each prospect to their latest cancellation timestamp
        latest_cancel = {}
        for prospect_id, cancel_ts in cancel_events:
            existing = latest_cancel.get(prospect_id)
            if existing is None or cancel_ts > existing:
                latest_cancel[prospect_id] = cancel_ts

        for event in tour_events:
            cancel_ts = latest_cancel.get(event.prospect_id)
            if cancel_ts is not None and cancel_ts > event.event_timestamp:
                continue
            upcoming_tours.append(_format_tour_time(event.planned_date_time))

    return {
        "unit_id": unit.pk,
        "address": address,
        "advertised_rent": str(advertised_rent) if advertised_rent is not None else None,
        "rentengine_status": rentengine_status,
        "days_on_market": days_on_market,
        "days_on_market_delta": days_on_market_delta,
        **metrics,
        **deltas,
        "touchpoints_calls": touchpoints_calls,
        "touchpoints_calls_delta": touchpoints_calls_delta,
        "touchpoints_texts": touchpoints_texts,
        "touchpoints_texts_delta": touchpoints_texts_delta,
        "upcoming_showings": upcoming_showings,
        "upcoming_showings_delta": upcoming_showings_delta,
        "leads_per_day": leads_per_day,
        "price_review_suggested": price_review_suggested,
        "dom_urgent": dom_urgent,
        "feedback": feedback,
        "upcoming_tours": upcoming_tours,
    }


def build_leasing_prompt(portfolio_name, unit_contexts, period_start, period_end):
    """
    Build the AI prompt for a portfolio-grain leasing note.

    Labeled plain-text facts per unit, then request JSON:
        {"intro": "...", "unit_summaries": {"<unit_id>": "..."}}
    """
    lines = [
        f'Write a weekly leasing update for the portfolio "{portfolio_name}".',
        f"Period: {period_start} to {period_end}.",
        "",
    ]

    for ctx in unit_contexts:
        lines.append(f"--- Unit {ctx['unit_id']}: {ctx['address']} ---")
        lines.append(f"  Status: {ctx['rentengine_status']}")

        rent = ctx["advertised_rent"]
        lines.append(f"  Advertised rent: {'$' + rent if rent is not None else 'unknown'}")

        # Days on market with urgency flag
        dom = ctx["days_on_market"]
        dom_str = str(dom) if dom is not None else "unknown"
        if ctx["dom_urgent"]:
            dom_str += " (35+ — recommend action, do not counsel patience)"
        if ctx["days_on_market_delta"] is not None and ctx["days_on_market_delta"] != 0:
            dom_str += f" ({ctx['days_on_market_delta']:+d} vs prior week)"
        lines.append(f"  Days on market: {dom_str}")

        # Leads per day with price review flag
        lpd = ctx["leads_per_day"]
        lpd_str = f"{lpd:.2f}"
        if ctx["price_review_suggested"]:
            lpd_str += " (below the 0.5 threshold — price review suggested)"
        lines.append(f"  Leads per day: {lpd_str}")

        # Event metrics with deltas
        for key, label in [
            ("new_leads", "New leads"),
            ("showings_scheduled", "Showings scheduled"),
            ("showings_completed", "Showings completed"),
            ("showings_canceled", "Showings canceled"),
            ("showings_missed", "Showings missed"),
            ("applications_received", "Applications received"),
        ]:
            lines.append(f"  {label}: {_format_val(ctx[key], ctx[f'{key}_delta'])}")

        # Nullable reporting metrics
        for key, label in [
            ("touchpoints_calls", "Touchpoint calls"),
            ("touchpoints_texts", "Touchpoint texts"),
            ("upcoming_showings", "Upcoming showings (RentEngine)"),
        ]:
            lines.append(f"  {label}: {_format_val(ctx[key], ctx.get(f'{key}_delta'))}")

        # Upcoming tours
        tours = ctx["upcoming_tours"]
        if tours:
            lines.append(f"  Upcoming tours: {', '.join(tours)}")
        else:
            lines.append("  Upcoming tours: none scheduled")

        # Feedback (names already stripped by _dedup_feedback)
        fb_items = ctx["feedback"]
        if fb_items:
            lines.append("  Showing feedback:")
            for fb in fb_items:
                lines.append(f'    - "{fb["feedback"]}"')
        else:
            lines.append("  Showing feedback: none")

        lines.append("")

    lines.append(
        "IMPORTANT: A field shown as 'unknown' means the value could not be "
        "fetched. Do NOT describe it as zero or none — say nothing about it or "
        "acknowledge it is unavailable."
    )
    lines.append("")
    lines.append(
        "Write one concise leasing note per unit (2-3 short paragraphs, flowing "
        "prose, no bullets). The intro is a 1-2 sentence portfolio-level summary "
        "of leasing activity this week. Return valid JSON only:\n"
        '{"intro": "...", "unit_summaries": {"<unit_id>": "..."}}'
    )

    return "\n".join(lines)


def generate_portfolio_leasing_note(
    portfolio, period_start, period_end, period_type="weekly", dry_run=False,
):
    """
    Generate a portfolio-grain leasing note (Layer 1: PortfolioLeasingNote).

    One Anthropic call per portfolio. Owner-agnostic.

    Returns None when there are no eligible units (portfolio has nothing listed).
    Returns dict: {"status": "...", ...} in all other cases:
      - "skipped"   — note is locked (approved / edited / has approved text)
      - "dry_run"   — contexts built, prompt built, no API call, no DB write
      - "generated" — note written to DB
    """
    from core.models import Unit
    from leasing.models import PortfolioLeasingNote, UnitLeasingSnapshot

    # Units in this portfolio that are Available or Waitlist
    units = list(
        Unit.objects.filter(
            property__portfolio=portfolio,
            rentengine_status__in=["Available", "Waitlist"],
        ).select_related("property")
    )

    if not units:
        return None

    # Filter to units with a snapshot for this period
    snapshot_map = {
        s.unit_id: s
        for s in UnitLeasingSnapshot.objects.filter(
            unit__in=units,
            period_start=period_start,
            period_end=period_end,
        )
    }
    eligible_units = [u for u in units if u.pk in snapshot_map]

    if not eligible_units:
        return None

    # Check for existing locked note
    existing = PortfolioLeasingNote.objects.filter(
        portfolio=portfolio,
        period_type=period_type,
        period_start=period_start,
    ).first()
    if existing and (
        existing.status == "approved"
        or existing.approved_generated_note
        or existing.is_edited
    ):
        logger.info(
            "leasing_note_locked",
            extra={
                "portfolio_name": portfolio.name,
                "note_id": existing.pk,
                "note_status": existing.status,
            },
        )
        return {"status": "skipped", "reason": "locked", "note_id": existing.pk}

    # Prior-period snapshots for deltas (derived from actual period length)
    period_length = (period_end - period_start).days + 1
    prior_start = period_start - timedelta(days=period_length)
    prior_end = period_start - timedelta(days=1)
    prior_map = {
        s.unit_id: s
        for s in UnitLeasingSnapshot.objects.filter(
            unit__in=eligible_units,
            period_start=prior_start,
            period_end=prior_end,
        )
    }

    # Build per-unit contexts
    unit_contexts = []
    for unit in eligible_units:
        ctx = build_unit_context(
            unit, snapshot_map[unit.pk], prior_map.get(unit.pk), period_end,
        )
        unit_contexts.append(ctx)

    user_prompt = build_leasing_prompt(
        portfolio.name, unit_contexts, period_start, period_end,
    )

    if dry_run:
        logger.info(
            "leasing_note_dry_run",
            extra={
                "portfolio_name": portfolio.name,
                "unit_count": len(unit_contexts),
            },
        )
        return {
            "status": "dry_run",
            "unit_contexts": unit_contexts,
            "prompt": user_prompt,
        }

    # --- Anthropic call ---
    voice_guide = _get_or_create_voice_guide("leasing")
    ai_result = _call_anthropic(voice_guide.instructions, user_prompt)

    # Build plain-text generated_note
    intro = ai_result.get("intro", "")
    unit_summaries = ai_result.get("unit_summaries", {})
    parts = []
    if intro:
        parts.append(intro)
    for ctx in unit_contexts:
        summary = unit_summaries.get(str(ctx["unit_id"]), "")
        if summary:
            parts.append(f"{ctx['address']}: {summary}")
    generated_note = "\n\n".join(parts)

    # Render HTML fragment
    template_units = []
    for ctx in unit_contexts:
        template_units.append({
            **ctx,
            "ai_note": unit_summaries.get(str(ctx["unit_id"]), ""),
        })

    notes_html = render_to_string(
        "comms/emails/leasing_fragment.html",
        {"units": template_units},
    )

    # Freeze snapshot
    frozen = {
        "unit_contexts": unit_contexts,
        "ai_result": ai_result,
    }

    # Upsert
    note, _ = PortfolioLeasingNote.objects.update_or_create(
        portfolio=portfolio,
        period_type=period_type,
        period_start=period_start,
        defaults={
            "period_end": period_end,
            "notes_html": notes_html,
            "generated_note": generated_note,
            "approved_generated_note": "",
            "unit_snapshot": frozen,
            "status": "draft",
            "is_edited": False,
        },
    )

    logger.info(
        "leasing_note_generated",
        extra={
            "portfolio_name": portfolio.name,
            "note_id": note.pk,
            "unit_count": len(unit_contexts),
        },
    )

    return {"status": "generated", "note_id": note.pk, "unit_count": len(unit_contexts)}
