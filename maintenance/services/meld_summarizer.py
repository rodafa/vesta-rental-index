"""
AI-powered meld summary generation for owner maintenance emails.

Uses Claude to generate concise, owner-facing summaries of maintenance work orders.
"""

import logging
import time
from decimal import Decimal

import anthropic
from django.conf import settings
from django.db.models import Sum

from maintenance.models import Meld

logger = logging.getLogger(__name__)

_CALL_PAUSE = 0.3


def summarize_meld(meld: Meld) -> tuple[str, str]:
    """
    Generate an AI summary for a single meld.

    Returns:
        (summary_text, summary_status) where status is "auto" or "needs_manual".
    """
    # Sparsity gate: if no description content at all, mark as needs_manual
    if not meld.brief_description and not meld.description:
        return ("", "needs_manual")

    # Calculate total cost from expenditures
    total_cost = meld.expenditures.filter(
        status__in=["BILLED", "APPROVED"]
    ).aggregate(total=Sum("amount"))["total"]

    prompt = build_meld_prompt(meld, total_cost)

    try:
        text = _call_claude(prompt)
        time.sleep(_CALL_PAUSE)
        return (text, "auto")
    except Exception:
        logger.exception("Claude call failed for meld %s", meld.property_meld_id)
        return ("", "needs_manual")


def build_meld_prompt(meld: Meld, total_cost: Decimal | None) -> str:
    """Build the Claude prompt for a single meld summary."""
    from integrations.property_meld.mappers import (
        EMAIL_CANCELED_BUCKET,
        EMAIL_CLOSED_BUCKET,
    )

    is_closed = meld.status in EMAIL_CLOSED_BUCKET
    is_canceled = meld.status in EMAIL_CANCELED_BUCKET
    is_open = not is_closed and not is_canceled

    lines = [
        "You are writing a 1-3 sentence maintenance update for a property owner.",
        "Be concise, factual, and plain-language. No jargon.",
        "",
        "WORK ORDER:",
        f"  Category: {meld.category} / {meld.work_type}" if meld.work_type else f"  Category: {meld.category}",
        f"  Description: {meld.brief_description}",
    ]

    if meld.description:
        lines.append(f"  {meld.description[:500]}")

    if meld.maintenance_notes:
        lines.append(f"  Maintenance notes: {meld.maintenance_notes[:300]}")

    if meld.assigned_vendor_name:
        lines.append(f"  Vendor: {meld.assigned_vendor_name}")

    if is_closed:
        completion = meld.completion_date or meld.marked_complete
        date_str = completion.strftime("%b %d, %Y") if completion else "Unknown"
        cost_str = f"${total_cost:,.2f}" if total_cost else "N/A"
        lines.append(f"  Completed: {date_str}, Cost: {cost_str}")
        if meld.completion_notes:
            lines.append(f"  Completion notes: {meld.completion_notes[:300]}")

    if meld.reason_cannot_complete:
        lines.append(f"  Could not complete: {meld.reason_cannot_complete[:300]}")

    lines.append("")

    if is_closed:
        lines.append("Write the summary now. State the cost for closed work orders.")
    elif is_canceled:
        lines.append("Write the summary now. Note that this work order was canceled.")
    else:
        lines.append("Write the summary now. For open work orders, do not mention cost.")

    return "\n".join(lines)


def _call_claude(prompt: str) -> str:
    """Call Claude and return the generated summary text."""
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def batch_summarize(meld_ids: list[int] | None = None, force: bool = False):
    """
    Generate summaries for a batch of melds.

    Args:
        meld_ids: Specific meld PKs to summarize. If None, summarize all
                  melds that need summaries.
        force: If True, re-summarize even if already done.

    Returns:
        dict: {summarized, skipped, failed}
    """
    queryset = Meld.objects.all()
    if meld_ids:
        queryset = queryset.filter(pk__in=meld_ids)

    if not force:
        # Only summarize melds without existing summaries
        queryset = queryset.filter(ai_summary="")

    summarized = 0
    skipped = 0
    failed = 0

    for meld in queryset.iterator():
        if not force and meld.ai_summary:
            skipped += 1
            continue

        summary_text, summary_status = summarize_meld(meld)

        if summary_status == "needs_manual" and not summary_text:
            failed += 1
        else:
            summarized += 1

        meld.ai_summary = summary_text
        meld.summary_status = summary_status
        meld.save(update_fields=["ai_summary", "summary_status", "updated_at"])

    return {"summarized": summarized, "skipped": skipped, "failed": failed}
