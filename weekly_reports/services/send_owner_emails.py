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
from properties.utils.name_classification import is_person_name
from weekly_reports.models import OwnerEmailSend
from weekly_reports.services.marketing_report import build_marketing_report
from weekly_reports.services.owner_email_html import build_owner_email_html

logger = logging.getLogger(__name__)


def group_properties_by_owner(week_start, week_end):
    """Group approved-note properties by owner, applying all exclusion filters.

    This is the single source of truth for both the send-preview UI and
    the actual send — both call this function so the preview is an exact
    mirror of what the send will do.

    Returns:
        dict with keys:
            recipients: list of dicts {owner, units_data, properties}
            skipped: list of dicts {name, email, reason, properties}
            report: the full marketing report (rows + benchmarks)
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
        return {"recipients": [], "skipped": [], "report": report}

    # Group rows by owner via Unit → Property → Portfolio → Owner
    approved_unit_ids = [r["unit_id"] for r in approved_rows]
    notes_qs = PropertyWeeklyNote.objects.filter(
        unit_id__in=approved_unit_ids, week_date=monday, approved=True
    ).select_related("unit__property__portfolio")

    # Build portfolio_id → [unit rows]
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

    # Now apply exclusion filters
    recipients = []
    skipped = []

    for oid, data in owner_portfolios.items():
        owner = data["owner"]
        units_data = data["units"]

        # Deduplicate units (owner may have multiple portfolios with same unit)
        seen_unit_ids = set()
        unique_units = []
        for u in units_data:
            if u["unit_id"] not in seen_unit_ids:
                seen_unit_ids.add(u["unit_id"])
                unique_units.append(u)
        units_data = unique_units

        # Extract property addresses for display
        properties_list = [u.get("address", "") for u in units_data]

        # Filter: LLC / entity name
        if not is_person_name(owner.name):
            skipped.append({
                "name": owner.name,
                "email": owner.email or "",
                "reason": "Flagged as entity (LLC/Corp/Trust)",
                "properties": properties_list,
            })
            continue

        # Filter: no email address on file
        if not owner.email:
            skipped.append({
                "name": owner.name,
                "email": "",
                "reason": "No email address on file",
                "properties": properties_list,
            })
            continue

        recipients.append({
            "owner": owner,
            "units_data": units_data,
            "properties": properties_list,
        })

    return {"recipients": recipients, "skipped": skipped, "report": report}


def build_send_preview(week_start=None, week_end=None):
    """Build a preview of who will receive emails and who is excluded.

    Used by the dashboard pre-send review page. Calls the same
    group_properties_by_owner() that the actual send uses.

    Args:
        week_start: Optional start date. Auto-derived if not provided.
        week_end: Optional end date. Auto-derived if not provided.

    Returns:
        dict with keys:
            recipients: list of {owner_id, name, email, properties}
            skipped: list of {name, email, reason, properties}
            total_recipients: int
            week_start: date used
            week_end: date used
    """
    if not week_start or not week_end:
        from weekly_reports.services.weekly_update import default_date_range
        week_start, week_end = default_date_range()

    grouped = group_properties_by_owner(week_start, week_end)

    recipients = [
        {
            "owner_id": r["owner"].id,
            "name": r["owner"].name,
            "email": r["owner"].email,
            "properties": r["properties"],
        }
        for r in grouped["recipients"]
    ]

    return {
        "recipients": recipients,
        "skipped": grouped["skipped"],
        "total_recipients": len(recipients),
        "week_start": week_start,
        "week_end": week_end,
    }


def send_approved_emails(
    week_start=None, week_end=None, user=None, dry_run=False,
    test_recipient=None, owner_id=None,
):
    """Send weekly leasing emails to all owners with approved notes.

    Args:
        week_start: Start date of reporting week. Auto-derived if not provided.
        week_end: End date of reporting week. Auto-derived if not provided.
        user: Optional User who triggered the send.
        dry_run: If True, log but don't actually send.
        test_recipient: When set, deliver every email to this address
            instead of the owner's, skip idempotency checks, and do
            not write OwnerEmailSend records.  Used for test sends.
        owner_id: When set, only process this owner ID.

    Returns:
        dict: {sent, skipped, failed, errors}.
    """
    if not week_start or not week_end:
        from weekly_reports.services.weekly_update import default_date_range
        week_start, week_end = default_date_range()

    # Monday anchor
    monday = week_start - timedelta(days=week_start.weekday())

    # Use the shared grouping function — same logic as preview
    grouped = group_properties_by_owner(week_start, week_end)
    all_recipients = grouped["recipients"]
    report = grouped["report"]
    benchmarks = report["benchmarks"]

    # Filter to a single owner when requested
    if owner_id:
        all_recipients = [r for r in all_recipients if r["owner"].id == owner_id]

    if not all_recipients:
        logger.info("No eligible recipients for week %s — nothing to send", monday)
        return {"sent": 0, "skipped": 0, "failed": 0, "errors": []}

    sent = 0
    skipped = 0
    failed = 0
    errors = []

    for recipient_data in all_recipients:
        owner = recipient_data["owner"]
        units_data = recipient_data["units_data"]

        # For test sends we don't need the owner's real address
        if not test_recipient and not owner.email:
            logger.warning("Owner %s has no email — skipping", owner.name)
            skipped += 1
            continue

        # Idempotency: skip if already sent — unless this is a test send
        if not test_recipient and OwnerEmailSend.objects.filter(
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

        # Determine recipient
        recipient = test_recipient or owner.email

        # Send via configured EMAIL_BACKEND (MailHog in dev, SendGrid in prod)
        msg_id = ""
        try:
            from django.core.mail import EmailMultiAlternatives

            week_label = f"{week_start.strftime('%b %d')} – {week_end.strftime('%b %d, %Y')}"
            msg = EmailMultiAlternatives(
                subject=f"Weekly Leasing Update — {week_label}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[recipient],
            )
            msg.attach_alternative(html, "text/html")
            msg.send()

            if hasattr(msg, "anymail_status"):
                msg_id = getattr(msg.anymail_status, "message_id", "") or ""

            if not test_recipient:
                OwnerEmailSend.objects.update_or_create(
                    owner=owner, week_date=monday,
                    defaults={
                        "status": "sent",
                        "sendgrid_message_id": msg_id,
                        "sent_at": timezone.now(),
                        "sent_by": user,
                        "error_detail": "",
                        "units_included": [u["unit_id"] for u in units_data],
                        "recipient_name": owner.name,
                        "recipient_email": owner.email,
                        "email_type": "weekly_leasing",
                    },
                )
            sent += 1
            logger.info(
                "Sent weekly email to %s (%s) — %d units, msg_id=%s",
                owner.name, recipient, len(units_data), msg_id,
            )

        except Exception as exc:
            error_msg = str(exc)[:500]
            logger.exception("Failed to send to %s (%s)", owner.name, recipient)
            errors.append({"owner": owner.name, "error": error_msg})
            failed += 1
            if not test_recipient:
                OwnerEmailSend.objects.update_or_create(
                    owner=owner, week_date=monday,
                    defaults={
                        "status": "failed",
                        "error_detail": error_msg,
                        "units_included": [u["unit_id"] for u in units_data],
                        "sent_by": user,
                        "recipient_name": owner.name,
                        "recipient_email": owner.email,
                        "email_type": "weekly_leasing",
                    },
                )

    logger.info(
        "Send complete: sent=%d skipped=%d failed=%d",
        sent, skipped, failed,
    )
    return {"sent": sent, "skipped": skipped, "failed": failed, "errors": errors}
