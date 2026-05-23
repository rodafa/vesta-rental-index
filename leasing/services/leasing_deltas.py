"""
Delta computation for DailyLeasingMetric cumulative fields.

Each cumulative count field (new_prospects, showings_completed, etc.) is
monotonically increasing *per listing cycle*.  The companion ``_delta``
field stores the per-day increment so date-range queries can simply SUM
the deltas.

Date gaps:  If a unit has rows for Mon and Thu but not Tue/Wed, the delta
on Thu captures the accumulated activity from Tue–Thu inclusive.  This is
by design — SUMming deltas over any window still yields the correct total.
"""

import logging

from leasing.models import DailyLeasingMetric

logger = logging.getLogger(__name__)

CUMULATIVE_FIELDS = [
    "new_prospects",
    "showings_completed",
    "applications_submitted",
    "showings_missed_or_failed",
    "applications_requested",
    "outbound_texts",
    "total_calls",
]
DELTA_FIELDS = [f"{f}_delta" for f in CUMULATIVE_FIELDS]


def recompute_deltas_for_unit(unit, from_date=None):
    """
    Recompute ``_delta`` fields for *unit* starting at *from_date*.

    None-safe carry-forward logic:
    - Maintains a ``carried`` dict of the last non-None value seen for
      each cumulative field.
    - If raw value is None (nullable fields), the carried value is used
      as the resolved value, defaulting to 0 if never seen.
    - Resets (resolved < prior) clamp delta to ``resolved`` and log.

    Idempotent — safe to call repeatedly.

    Returns ``{"rows_examined": N, "rows_updated": N, "resets_detected": N}``.
    """
    qs = DailyLeasingMetric.objects.filter(unit=unit).order_by("date")
    if from_date is not None:
        # We need the row *before* from_date to compute the first delta.
        prior_row = (
            DailyLeasingMetric.objects.filter(unit=unit, date__lt=from_date)
            .order_by("-date")
            .first()
        )
        qs = qs.filter(date__gte=from_date)
    else:
        prior_row = None

    rows = list(qs)
    if not rows:
        return {"rows_examined": 0, "rows_updated": 0, "resets_detected": 0}

    # Seed carried + prior_resolved from the row before the window.
    carried = {f: 0 for f in CUMULATIVE_FIELDS}
    prior_resolved = {}

    if prior_row is not None:
        for field in CUMULATIVE_FIELDS:
            raw = getattr(prior_row, field)
            if raw is not None:
                carried[field] = raw
                prior_resolved[field] = raw
            else:
                prior_resolved[field] = carried[field]

    rows_updated = 0
    resets_detected = 0
    to_update = []

    for row in rows:
        changed = False
        for field, delta_field in zip(CUMULATIVE_FIELDS, DELTA_FIELDS):
            raw = getattr(row, field)

            # Resolve None via carry-forward
            if raw is not None:
                carried[field] = raw
                resolved = raw
            else:
                resolved = carried.get(field, 0)

            # Compute delta
            if field not in prior_resolved:
                delta = resolved
            elif resolved >= prior_resolved[field]:
                delta = resolved - prior_resolved[field]
            else:
                # Reset detected — new listing cycle
                delta = resolved
                resets_detected += 1
                logger.info(
                    "Reset detected: unit=%s date=%s field=%s prior=%s current=%s",
                    unit.pk,
                    row.date,
                    field,
                    prior_resolved[field],
                    resolved,
                )

            prior_resolved[field] = resolved

            if getattr(row, delta_field) != delta:
                setattr(row, delta_field, delta)
                changed = True

        if changed:
            to_update.append(row)
            rows_updated += 1

    if to_update:
        DailyLeasingMetric.objects.bulk_update(to_update, DELTA_FIELDS)

    return {
        "rows_examined": len(rows),
        "rows_updated": rows_updated,
        "resets_detected": resets_detected,
    }
