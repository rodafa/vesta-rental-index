"""
Service functions for the weekly team blurb on owner leasing emails.
"""

import json
import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)


def week_start_for(d: date) -> date:
    """Return the Monday of the ISO week containing *d*."""
    return d - timedelta(days=d.weekday())


def next_send_date(from_date: date | None = None) -> date:
    """Return the next date on or after *from_date* matching OWNER_EMAIL_SEND_WEEKDAY."""
    from django.conf import settings

    if from_date is None:
        from_date = date.today()

    send_weekday = getattr(settings, "OWNER_EMAIL_SEND_WEEKDAY", 0)
    days_ahead = (send_weekday - from_date.weekday()) % 7
    if days_ahead == 0:
        return from_date  # today IS the send day
    return from_date + timedelta(days=days_ahead)


def target_send_week(from_date: date | None = None) -> date:
    """Return the Monday of the week the next send lands in."""
    return week_start_for(next_send_date(from_date))


def get_blurb_for_week(week_start: date):
    """Return the WeeklyLeasingBlurb for the given Monday, or None.

    *week_start* must already be the Monday of the desired week —
    callers are responsible for resolving it via ``week_start_for()``.
    """
    from weekly_reports.models import WeeklyLeasingBlurb

    return WeeklyLeasingBlurb.objects.filter(week_start=week_start).first()


def upsert_blurb(week_start: date, body: str, user):
    """Create or update the blurb for the week beginning *week_start*.

    *week_start* must already be a Monday.
    Returns the saved WeeklyLeasingBlurb instance.
    """
    from weekly_reports.models import WeeklyLeasingBlurb

    blurb, created = WeeklyLeasingBlurb.objects.update_or_create(
        week_start=week_start,
        defaults={"body": body, "last_edited_by": user},
    )
    logger.info(
        json.dumps({
            "event": "blurb_upsert",
            "week_start": week_start.isoformat(),
            "user": (user.get_full_name() or user.username) if user else None,
            "created": created,
            "body_length": len(body),
        })
    )
    return blurb
