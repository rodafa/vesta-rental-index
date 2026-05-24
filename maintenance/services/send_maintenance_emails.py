"""
Send maintenance summary emails to owners.

Mirrors the pattern from weekly_reports/services/send_owner_emails.py.
"""

import logging
from datetime import date, timedelta

from django.conf import settings
from django.utils import timezone

from maintenance.models import MaintenanceEmailMeld, MaintenanceEmailSend, Meld
from maintenance.services.owner_email_builder import (
    build_maintenance_email_data,
    build_maintenance_email_html,
)
from properties.models import Owner

logger = logging.getLogger(__name__)


def send_maintenance_emails(
    date_start: date,
    date_end: date,
    user=None,
    dry_run: bool = False,
    test_recipient: str | None = None,
    owner_id: int | None = None,
):
    """
    Send weekly maintenance summary emails to owners.

    Args:
        date_start: Start of reporting period.
        date_end: End of reporting period.
        user: Optional User who triggered the send.
        dry_run: If True, log but don't send.
        test_recipient: Override recipient email (bypasses idempotency).
        owner_id: When set, only send to this owner.

    Returns:
        dict: {sent, skipped, failed, errors}
    """
    # Monday anchor for idempotency
    monday = date_start - timedelta(days=date_start.weekday())

    # Get owners to send to
    owners_qs = Owner.objects.filter(is_active=True)
    if owner_id:
        owners_qs = owners_qs.filter(pk=owner_id)

    owners = list(owners_qs.prefetch_related("portfolios"))

    sent = 0
    skipped = 0
    failed = 0
    errors = []

    for owner in owners:
        # Check email
        if not test_recipient and not owner.email:
            logger.warning("Owner %s has no email — skipping", owner.name)
            skipped += 1
            continue

        # Idempotency: skip if already sent (unless test send)
        if not test_recipient and MaintenanceEmailSend.objects.filter(
            owner=owner, week_date=monday, status="sent"
        ).exists():
            logger.info(
                "Already sent maintenance email to %s for week %s — skipping",
                owner.name, monday,
            )
            skipped += 1
            continue

        # Build email data
        email_data = build_maintenance_email_data(owner, date_start, date_end)

        # Skip if no activity at all
        if (
            not email_data["open_melds"]
            and not email_data["closed_melds"]
            and not email_data["canceled_melds"]
        ):
            logger.info("No maintenance activity for %s — skipping", owner.name)
            skipped += 1
            continue

        if dry_run:
            logger.info(
                "[DRY RUN] Would send maintenance email to %s (%s) — "
                "open=%d closed=%d canceled=%d",
                owner.name, owner.email,
                len(email_data["open_melds"]),
                len(email_data["closed_melds"]),
                len(email_data["canceled_melds"]),
            )
            skipped += 1
            continue

        # Render HTML
        html = build_maintenance_email_html(owner, email_data, date_start, date_end)

        # Send
        recipient = test_recipient or owner.email
        msg_id = ""

        try:
            from django.core.mail import EmailMultiAlternatives

            week_label = (
                f"{date_start.strftime('%b %d')} – {date_end.strftime('%b %d, %Y')}"
            )
            msg = EmailMultiAlternatives(
                subject=f"Weekly Maintenance Update — {week_label}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[recipient],
            )
            msg.attach_alternative(html, "text/html")
            msg.send()

            if hasattr(msg, "anymail_status"):
                msg_id = getattr(msg.anymail_status, "message_id", "") or ""

            if not test_recipient:
                email_send, _ = MaintenanceEmailSend.objects.update_or_create(
                    owner=owner,
                    week_date=monday,
                    defaults={
                        "status": "sent",
                        "sendgrid_message_id": msg_id,
                        "sent_at": timezone.now(),
                        "sent_by": user,
                        "error_detail": "",
                        "melds_included": (
                            [m["id"] for m in email_data["open_melds"]]
                            + [m["id"] for m in email_data["closed_melds"]]
                            + [m["id"] for m in email_data["canceled_melds"]]
                        ),
                    },
                )
                # Write frozen snapshots
                _create_meld_snapshots(email_send, email_data, owner)
            sent += 1
            logger.info(
                "Sent maintenance email to %s (%s) — open=%d closed=%d, msg_id=%s",
                owner.name, recipient,
                len(email_data["open_melds"]),
                len(email_data["closed_melds"]),
                msg_id,
            )

        except Exception as exc:
            error_msg = str(exc)[:500]
            logger.exception(
                "Failed to send maintenance email to %s (%s)", owner.name, recipient
            )
            errors.append({"owner": owner.name, "error": error_msg})
            failed += 1
            if not test_recipient:
                MaintenanceEmailSend.objects.update_or_create(
                    owner=owner,
                    week_date=monday,
                    defaults={
                        "status": "failed",
                        "error_detail": error_msg,
                        "sent_by": user,
                        "melds_included": [],
                    },
                )

    logger.info(
        "Maintenance email send complete: sent=%d skipped=%d failed=%d",
        sent, skipped, failed,
    )
    return {"sent": sent, "skipped": skipped, "failed": failed, "errors": errors}


def _create_meld_snapshots(email_send, email_data, owner):
    """
    Write one MaintenanceEmailMeld row per meld included in the send.
    Frozen copies — never updated later.
    """
    snapshots = []
    for section, melds in [
        ("open", email_data["open_melds"]),
        ("closed", email_data["closed_melds"]),
        ("canceled", email_data["canceled_melds"]),
    ]:
        for m in melds:
            meld_obj = Meld.objects.select_related(
                "unit_fk__property__portfolio"
            ).filter(pk=m["id"]).first()

            # Derive portfolio per meld from unit → property → portfolio
            portfolio = None
            if meld_obj and meld_obj.unit_fk and meld_obj.unit_fk.property:
                portfolio = meld_obj.unit_fk.property.portfolio
            portfolio_name = portfolio.name if portfolio else ""

            snapshots.append(
                MaintenanceEmailMeld(
                    email_send=email_send,
                    meld=meld_obj,
                    meld_reference_id=m.get("reference_id", ""),
                    summary_text=m.get("summary", ""),
                    cost=m.get("cost") or None,
                    status_at_send=m.get("status", ""),
                    section=section,
                    portfolio=portfolio,
                    portfolio_name=portfolio_name,
                    unit_label=m.get("unit_address", ""),
                    property_address=m.get("unit_address", ""),
                )
            )

    if snapshots:
        MaintenanceEmailMeld.objects.bulk_create(snapshots)
