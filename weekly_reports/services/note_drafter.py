"""
AI note drafting using Claude — enhanced prompt with voice guide, portfolio
benchmark, LeadSimple context, and history.
"""

import logging
import time

import anthropic
from django.conf import settings

from weekly_reports.services.voice_guide import load_voice_guide

logger = logging.getLogger(__name__)

# Pause between Claude calls to avoid rate limits
_CALL_PAUSE = 1.5


def draft_note(unit_data):
    """Draft a weekly note for a single unit using Claude.

    Args:
        unit_data: dict with keys:
            address, beds, baths, list_price, dom, leads, showings, apps,
            feedback_text, active_deals, resolved_deals, move_ins,
            history, benchmark

    Returns the drafted note text, or None on failure.
    """
    prompt = build_prompt(unit_data)

    try:
        text = _call_claude(prompt)
        time.sleep(_CALL_PAUSE)
        return text
    except Exception:
        logger.exception("Claude call failed for %s", unit_data.get("address", "?"))
        return None


def build_prompt(unit_data):
    """Build the full Claude prompt for a single unit."""
    u = unit_data
    voice_guide = load_voice_guide()

    lines = []

    # Voice guide
    if voice_guide:
        lines += [voice_guide, ""]

    # Property info
    lines += [
        "PROPERTY:",
        f"  Address: {u['address']}",
        f"  Beds/Baths: {u.get('beds', '?')}BR / {u.get('baths', '?')}BA",
        f"  Listed at: ${u.get('list_price', 0)}/mo",
        "",
        "THIS WEEK'S ACTIVITY:",
        f"  Days on market: {u.get('dom', 0)}",
        f"  New leads: {u.get('leads', 0)}",
        f"  Showings completed: {u.get('showings', 0)}",
        f"  Applications received: {u.get('apps', 0)}",
    ]

    # Portfolio benchmark
    bm = u.get("benchmark")
    if bm and bm.get("unit_count", 0) > 1:
        lines += [
            "",
            "PORTFOLIO BENCHMARK (average across active listings):",
            f"  Avg leads/unit: {bm['avg_leads']}",
            f"  Avg showings/unit: {bm['avg_showings']}",
            f"  Avg apps/unit: {bm['avg_apps']}",
            f"  Active units: {bm['unit_count']}",
        ]

    # Showing feedback
    if u.get("feedback_text"):
        lines += [
            "",
            "SHOWING FEEDBACK:",
            u["feedback_text"][:800],
        ]

    # LeadSimple — active applications
    if u.get("active_deals"):
        lines += ["", "ACTIVE LEADSIMPLE PROCESSES:"]
        for d in u["active_deals"]:
            lines.append(
                f"- {d['name']} | Stage: {d['stage_name']} | Since: {d.get('created_at', '?')}"
            )
            if d.get("comments"):
                lines.append(f"  Notes: {d['comments']}")

    # LeadSimple — resolved this week
    if u.get("resolved_deals"):
        lines += ["", "RESOLVED THIS WEEK:"]
        for d in u["resolved_deals"]:
            lines.append(
                f"- {d['name']} | Stage: {d['stage_name']} | Closed: {d.get('closed_at', '?')}"
            )

    # Move-ins
    if u.get("move_ins"):
        lines += ["", "MOVE-INS THIS WEEK:"]
        for d in u["move_ins"]:
            lines.append(f"- {d['name']} | Stage: {d['stage_name']}")

    # Recent history
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
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()
