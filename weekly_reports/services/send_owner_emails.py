"""
Send approved weekly leasing emails to owners.

Runs synchronously — typically 16-50 emails, finishes quickly.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from dashboard.models import PropertyWeeklyNote
from properties.models import Owner
from weekly_reports.models import OwnerEmailSend
from weekly_reports.services.marketing_report import build_marketing_report
from weekly_reports.services.owner_email_html import build_owner_email_html

logger = logging.getLogger(__name__)


def send_approved_emails(week_start, week_end, user=None, dry_run=False):
    """Send weekly leasing emails to all owners with approved notes.

    Args:
        week_start: Start date of reporting week.
        week_end: End date of reporting week.
        user: Optional User who triggered the send.
        dry_run: If True, log but don't actually send.

    Returns:
        dict: {sent, skipped, failed, errors}.
    """
    # Monday anchor — must match PropertyWeeklyNote.week_date convention
    monday = week_start - timedelta(days=week_start.weekday())

    # Build the full report data (all rows + benchmarks)
    report = build_marketing_report(week_start, week_end)
    rows = report["rows"]
    benchmarks = report["benchmarks"]

    # Filter to approved notes only
    approved_rows = [r for r in rows if r.get("note_approved")]
    if not approved_rows:
        logger.info("No approved notes for week %s — nothing to send", monday)
        return {"sent": 0, "skipped": 0, "failed": 0, "errors": []}

    # Group rows by owner
    # Unit → Property → Portfolio → Owner (via the row data)
    approved_unit_ids = [r["unit_id"] for r in approved_rows]
    notes_qs = PropertyWeeklyNote.objects.filter(
        unit_id__in=approved_unit_ids, week_date=monday, approved=True
    ).select_related("unit__property__portfolio")

    # Build portfolio_id → [unit rows] and find owners
    portfolio_units = {}
    for note in notes_qs:
        portfolio = note.unit.property.portfolio if note.unit.property else None
        if not portfolio:
            continue
        pid = portfolio.id
        row = next((r for r in approved_rows if r["unit_id"] == note.unit_id), None)
        if row:
            portfolio_units.setdefault(pid, []).append(row)

    # Get owners per portfolio
    portfolio_ids = list(portfolio_units.keys())
    owner_portfolios = {}
    if portfolio_ids:
        for owner in Owner.objects.filter(
            portfolios__id__in=portfolio_ids, is_active=True
        ).prefetch_related("portfolios").distinct():
            for p in owner.portfolios.filter(id__in=portfolio_ids):
                owner_portfolios.setdefault(owner.id, {
                    "owner": owner, "units": []
                })["units"].extend(portfolio_units.get(p.id, []))

    sent = 0
    skipped = 0
    failed = 0
    errors = []

    for owner_id, data in owner_portfolios.items():
        owner = data["owner"]
        units_data = data["units"]

        if not owner.email:
            logger.warning("Owner %s has no email — skipping", owner.name)
            skipped += 1
            continue

        # Deduplicate units (owner may have multiple portfolios with same unit)
        seen_unit_ids = set()
        unique_units = []
        for u in units_data:
            if u["unit_id"] not in seen_unit_ids:
                seen_unit_ids.add(u["unit_id"])
                unique_units.append(u)
        units_data = unique_units

        # Idempotency: skip if already sent for this owner + week
        if OwnerEmailSend.objects.filter(
            owner=owner, week_date=monday, status="sent"
        ).exists():
            logger.info("Already sent to %s for week %s — skipping", owner.name, monday)
            skipped += 1
            continue

        if dry_run:
            logger.info(
                "[DRY RUN] Would send to %s (%s) — %d units",
                owner.name, owner.email, len(units_data),
            )
            skipped += 1
            continue

        # Build HTML
        html = build_owner_email_html(owner, units_data, benchmarks, week_start, week_end)

        # Send via AnymailMessage
        msg_id = ""
        try:
            from anymail.message import AnymailMessage
        except ImportError:
            error_msg = "SendGrid not configured (SENDGRID_API_KEY missing)"
            logger.error(error_msg)
            errors.append({"owner": owner.name, "error": error_msg})
            failed += 1
            OwnerEmailSend.objects.update_or_create(
                owner=owner, week_date=monday,
                defaults={
                    "status": "failed",
                    "error_detail": error_msg,
                    "units_included": [u["unit_id"] for u in units_data],
                    "sent_by": user,
                },
            )
            continue

        try:
            week_label = f"{week_start.strftime('%b %d')} – {week_end.strftime('%b %d, %Y')}"
            msg = AnymailMessage(
                subject=f"Weekly Leasing Update — {week_label}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[owner.email],
            )
            msg.attach_alternative(html, "text/html")
            msg.send()

            if hasattr(msg, "anymail_status"):
                msg_id = getattr(msg.anymail_status, "message_id", "") or ""

            OwnerEmailSend.objects.update_or_create(
                owner=owner, week_date=monday,
                defaults={
                    "status": "sent",
                    "sendgrid_message_id": msg_id,
                    "sent_at": timezone.now(),
                    "sent_by": user,
                    "error_detail": "",
                    "units_included": [u["unit_id"] for u in units_data],
                },
            )
            sent += 1
            logger.info(
                "Sent weekly email to %s (%s) — %d units, msg_id=%s",
                owner.name, owner.email, len(units_data), msg_id,
            )

        except Exception as exc:
            error_msg = str(exc)[:500]
            logger.exception("Failed to send to %s (%s)", owner.name, owner.email)
            errors.append({"owner": owner.name, "error": error_msg})
            failed += 1
            OwnerEmailSend.objects.update_or_create(
                owner=owner, week_date=monday,
                defaults={
                    "status": "failed",
                    "error_detail": error_msg,
                    "units_included": [u["unit_id"] for u in units_data],
                    "sent_by": user,
                },
            )

    logger.info(
        "Send complete: sent=%d skipped=%d failed=%d",
        sent, skipped, failed,
    )
    return {"sent": sent, "skipped": skipped, "failed": failed, "errors": errors}
