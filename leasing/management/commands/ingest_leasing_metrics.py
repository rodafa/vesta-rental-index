"""
Ingest daily leasing performance from RentEngine API.

Usage:
    python manage.py ingest_leasing_metrics [--date YYYY-MM-DD] [--unit-id N]

Defaults to yesterday and all active units (those with status="active"
on that day's DailyUnitSnapshot and rentengine_id set).
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from leasing.services.leasing_performance import ingest_day


class Command(BaseCommand):
    help = "Ingest daily leasing metrics from RentEngine for a given date."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Date to ingest (YYYY-MM-DD). Defaults to yesterday.",
        )
        parser.add_argument(
            "--unit-id",
            type=int,
            default=None,
            help="RentEngine unit ID to ingest. Defaults to all active units.",
        )

    def handle(self, *args, **options):
        from datetime import date as date_type

        if options["date"]:
            target_date = date_type.fromisoformat(options["date"])
        else:
            target_date = (timezone.now() - timedelta(days=1)).date()

        unit_ids = [options["unit_id"]] if options["unit_id"] else None

        self.stdout.write(
            f"Ingesting leasing metrics for {target_date}"
            + (f" (unit {options['unit_id']})" if options["unit_id"] else " (all active units)")
            + "\n"
        )

        result = ingest_day(target_date, unit_ids=unit_ids)

        self.stdout.write(
            f"Units processed: {result['units_processed']}\n"
            f"Metrics upserted: {result['metrics_upserted']}\n"
            f"Feedback upserted: {result['feedback_upserted']}\n"
        )

        if result["errors"]:
            self.stderr.write(f"Errors ({len(result['errors'])}):\n")
            for err in result["errors"]:
                self.stderr.write(f"  {err}\n")
