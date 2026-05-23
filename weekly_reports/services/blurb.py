"""
Service functions for the weekly team blurb on owner leasing emails.
"""

import json
import logging
from datetime import date

logger = logging.getLogger(__name__)


def get_blurb_for_week(week_date: date):
    """Return the OwnerEmailBlurb for the given Monday, or None.

    *week_date* must already be the Monday of the desired week —
    callers are responsible for computing the Monday anchor.
    """
    from weekly_reports.models import OwnerEmailBlurb

    return OwnerEmailBlurb.objects.filter(week_date=week_date).first()


def upsert_blurb(week_date: date, body: str, user):
    """Create or update the blurb for the week beginning *week_date*.

    *week_date* must already be a Monday.
    Returns the saved OwnerEmailBlurb instance.
    """
    from weekly_reports.models import OwnerEmailBlurb

    blurb, created = OwnerEmailBlurb.objects.update_or_create(
        week_date=week_date,
        defaults={"body": body, "last_edited_by": user},
    )
    logger.info(
        json.dumps({
            "event": "blurb_upsert",
            "week_date": week_date.isoformat(),
            "user": (user.get_full_name() or user.username) if user else None,
            "created": created,
            "body_length": len(body),
        })
    )
    return blurb
