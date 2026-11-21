"""Checkout / return service layer for KeyBot.

All writes run inside transaction.atomic with select_for_update on Tag.
User-facing refusals return a plain error string; callers post it to Slack.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from keys.models import Checkout, KeyChange, Tag

logger = logging.getLogger(__name__)


def checkout_tag(tag: Tag, slack_user_id: str, display_name: str):
    """Check out a tag. Returns Checkout on success, str on refusal."""
    with transaction.atomic():
        tag = Tag.objects.select_for_update().get(pk=tag.pk)

        if tag.status == "retired":
            return f"{tag.key_id} is retired and cannot be checked out."
        if tag.status == "missing":
            return f"{tag.key_id} is marked missing."

        open_co = Checkout.objects.filter(
            tag=tag, returned_at__isnull=True,
        ).select_related("tag").first()
        if open_co:
            return (
                f"{tag.key_id} is already checked out"
                f" to {open_co.checked_out_by_name}."
            )

        old_status = tag.status
        tag.status = "checked_out"
        tag.save(update_fields=["status", "updated_at"])

        now = timezone.now()
        co = Checkout.objects.create(
            tag=tag,
            checked_out_by_slack_id=slack_user_id,
            checked_out_by_name=display_name,
            checked_out_at=now,
        )

        KeyChange.objects.create(
            tag=tag,
            unit=tag.unit,
            field="status",
            old_value=old_status,
            new_value="checked_out",
            changed_by=slack_user_id,
            reason="checkout",
        )

        logger.info(
            f"key_checked_out tag={tag.key_id}"
            f" by={slack_user_id} name={display_name}"
        )
        return co


def return_tag(tag: Tag, slack_user_id: str):
    """Return a checked-out tag. Returns the closed Checkout, or None."""
    with transaction.atomic():
        tag = Tag.objects.select_for_update().get(pk=tag.pk)

        open_co = Checkout.objects.filter(
            tag=tag, returned_at__isnull=True,
        ).first()
        if open_co is None:
            return None

        now = timezone.now()
        open_co.returned_at = now
        open_co.save(update_fields=["returned_at"])

        old_status = tag.status
        tag.status = "on_hook"
        tag.save(update_fields=["status", "updated_at"])

        KeyChange.objects.create(
            tag=tag,
            unit=tag.unit,
            field="status",
            old_value=old_status,
            new_value="on_hook",
            changed_by=slack_user_id,
            reason="return",
        )

        logger.info(
            f"key_returned tag={tag.key_id} by={slack_user_id}"
        )
        return open_co


def snooze_reminder(checkout: Checkout) -> None:
    """User clicked 'Still have it' — reset reminder timer."""
    checkout.last_reminder_at = timezone.now()
    checkout.save(update_fields=["last_reminder_at"])


def record_reminder_sent(checkout: Checkout) -> None:
    """System sent a reminder — update timer and increment count."""
    checkout.last_reminder_at = timezone.now()
    checkout.reminder_count += 1
    checkout.save(update_fields=["last_reminder_at", "reminder_count"])


def due_reminders(now):
    """Open checkouts >= 24h old, not reminded in last 24h.

    Returns a QuerySet of Checkout with select_related tag/unit/property.
    """
    cutoff = now - timedelta(hours=24)
    return (
        Checkout.objects
        .filter(returned_at__isnull=True, checked_out_at__lte=cutoff)
        .filter(
            Q(last_reminder_at__isnull=True) | Q(last_reminder_at__lte=cutoff),
        )
        .select_related("tag", "tag__unit", "tag__unit__property")
        .order_by("checked_out_at")
    )


def open_checkouts():
    """All open checkouts, oldest first."""
    return (
        Checkout.objects
        .filter(returned_at__isnull=True)
        .select_related("tag", "tag__unit", "tag__unit__property")
        .order_by("checked_out_at")
    )


def in_reminder_window(now_et) -> bool:
    """True when the ET hour is 9–16 inclusive (9:00 AM – 4:59 PM)."""
    return 9 <= now_et.hour <= 16
