"""
Utilities for pipeline gap detection and self-healing backfill.
"""

from datetime import date, timedelta

from django.utils import timezone

from market.models import DailyUnitSnapshot


def detect_missing_dates(lookback_days=7):
    """
    Detect dates in the last `lookback_days` that have no DailyUnitSnapshot records.

    Returns a sorted list of missing dates (oldest first), excluding today.
    """
    today = timezone.now().date()
    start = today - timedelta(days=lookback_days)

    expected = {start + timedelta(days=i) for i in range(lookback_days)}
    expected.discard(today)  # today will be handled by the normal pipeline run

    existing = set(
        DailyUnitSnapshot.objects.filter(
            snapshot_date__gte=start,
            snapshot_date__lt=today,
        )
        .values_list("snapshot_date", flat=True)
        .distinct()
    )

    missing = sorted(expected - existing)
    return missing
