"""
Management command to backfill _delta fields on all DailyLeasingMetric rows.

Iterates every unit that has at least one metric row and calls
recompute_deltas_for_unit().  Idempotent — safe to run repeatedly.

Usage:
    python manage.py backfill_leasing_deltas
"""

import json
import logging

from django.core.management.base import BaseCommand

from leasing.models import DailyLeasingMetric
from leasing.services.leasing_deltas import recompute_deltas_for_unit
from properties.models import Unit

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Backfill per-day delta fields on DailyLeasingMetric."

    def handle(self, *args, **options):
        unit_ids = (
            DailyLeasingMetric.objects.values_list("unit_id", flat=True)
            .distinct()
        )
        units = Unit.objects.filter(pk__in=unit_ids)

        totals = {
            "units_processed": 0,
            "rows_examined": 0,
            "rows_updated": 0,
            "resets_detected": 0,
        }

        for unit in units.iterator():
            result = recompute_deltas_for_unit(unit)
            totals["units_processed"] += 1
            totals["rows_examined"] += result["rows_examined"]
            totals["rows_updated"] += result["rows_updated"]
            totals["resets_detected"] += result["resets_detected"]

        summary = json.dumps(totals)
        logger.info("backfill_leasing_deltas complete: %s", summary)
        self.stdout.write(self.style.SUCCESS(f"Done: {summary}"))
