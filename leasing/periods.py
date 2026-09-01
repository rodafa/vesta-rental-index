"""
Leasing period helpers — weekly cadence utilities.

The leasing reporting cadence is Tuesday through Monday. The owner email
is sent on Tuesday, covering the week that ended the previous day (Monday).
"""

from __future__ import annotations

import datetime

from django.core.management.base import CommandError

DAY_NAMES = [
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
]


def last_complete_week(
    today: datetime.date | None = None,
) -> tuple[datetime.date, datetime.date]:
    """Return the most recent complete Tuesday–Monday period.

    The period ends on the most recent Monday *strictly before* today.

    Examples (all three return (2026-08-25, 2026-08-31)):
        last_complete_week(date(2026, 9, 1))  # Tuesday  — yesterday was the Monday
        last_complete_week(date(2026, 9, 2))  # Wednesday — rolls back to Mon Aug 31
        last_complete_week(date(2026, 9, 7))  # Monday   — this week is NOT complete;
                                               #            still rolls back to Aug 31
    """
    if today is None:
        today = datetime.date.today()

    # Start from yesterday so that "today is Monday" never counts as
    # the end of a complete week.
    yesterday = today - datetime.timedelta(days=1)
    # Roll back to the most recent Monday on or before yesterday.
    # weekday(): Monday=0, Tuesday=1, …, Sunday=6.
    period_end = yesterday - datetime.timedelta(days=yesterday.weekday())
    period_start = period_end - datetime.timedelta(days=6)
    return period_start, period_end


def resolve_period(
    start_str: str | None,
    end_str: str | None,
    force: bool,
    stdout=None,
) -> tuple[datetime.date, datetime.date]:
    """Resolve and validate the --start / --end period for a leasing command.

    Cases:
      - Neither supplied  → defaults to last_complete_week().
      - Both supplied     → parses and validates (unless *force*).
      - Only one supplied → CommandError (ambiguous intent).

    Validation (skipped when *force* is True):
      - Period must be exactly 7 days inclusive.
      - start must be a Tuesday.

    *stdout*, when provided, receives informational messages via .write().
    Raises CommandError on any validation failure.
    """
    have_start = start_str is not None
    have_end = end_str is not None

    # --- Only one supplied → reject immediately ---
    if have_start != have_end:
        raise CommandError(
            "Supply both --start and --end, or neither."
        )

    # --- Neither supplied → default ---
    if not have_start:
        start, end = last_complete_week()
        if stdout:
            stdout.write(
                f"Period not specified, using last complete week: "
                f"{start} to {end}."
            )
        return start, end

    # --- Both supplied → parse ---
    try:
        start = datetime.date.fromisoformat(start_str)
    except ValueError as exc:
        raise CommandError(f"Invalid --start date: {exc}") from exc

    try:
        end = datetime.date.fromisoformat(end_str)
    except ValueError as exc:
        raise CommandError(f"Invalid --end date: {exc}") from exc

    if start > end:
        raise CommandError(
            f"--start ({start}) must not be after --end ({end})."
        )

    span_days = (end - start).days + 1  # inclusive count
    start_weekday = start.weekday()     # Monday=0, Tuesday=1

    # --- --force: skip validation, warn loudly ---
    if force:
        if stdout:
            stdout.write(
                f"WARNING: --force used. Period is {span_days} day(s) "
                f"starting on {DAY_NAMES[start_weekday]}. Off-grid "
                f"periods break week-over-week deltas."
            )
        return start, end

    # --- Validate length ---
    if span_days != 7:
        raise CommandError(
            f"Period must be exactly 7 days (inclusive). "
            f"Got {span_days} day(s) ({start} to {end}). "
            f"Use --force for intentional backfills; off-grid periods "
            f"break week-over-week deltas."
        )

    # --- Validate Tuesday start ---
    if start_weekday != 1:  # 1 = Tuesday
        days_since_tue = (start_weekday - 1) % 7
        prev_tue = start - datetime.timedelta(days=days_since_tue)
        next_tue = prev_tue + datetime.timedelta(days=7)
        nearest = prev_tue if days_since_tue <= 3 else next_tue

        raise CommandError(
            f"Period must start on a Tuesday. "
            f"Got {DAY_NAMES[start_weekday]} ({start}). "
            f"Nearest valid Tuesday: {nearest}. "
            f"Use --force for intentional backfills; off-grid periods "
            f"break week-over-week deltas."
        )

    return start, end
