import logging
from datetime import date

from django.core.management.base import BaseCommand

from integrations.boompay.services import DLMApplicationCountService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Populate DailyLeasingMetric.applications_submitted from "
        "BoomScreen ScreeningApplication records (Eastern day boundaries)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--start-date",
            type=str,
            default=None,
            help="Start date in YYYY-MM-DD format (default: yesterday)",
        )
        parser.add_argument(
            "--end-date",
            type=str,
            default=None,
            help="End date in YYYY-MM-DD format (default: yesterday)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Log what would change without writing to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        start_date = (
            date.fromisoformat(options["start_date"])
            if options["start_date"]
            else None
        )
        end_date = (
            date.fromisoformat(options["end_date"])
            if options["end_date"]
            else None
        )

        if dry_run:
            self.stdout.write("=== DRY RUN: DLM application count sync ===")

        service = DLMApplicationCountService()
        result = service.sync(
            start_date=start_date,
            end_date=end_date,
            dry_run=dry_run,
        )

        self.stdout.write(self.style.SUCCESS(
            f"DLM application counts: "
            f"{result['created']} created, {result['updated']} updated, "
            f"{result['zero_filled']} zero-filled, "
            f"{result['skipped_null_unit']} skipped (null unit), "
            f"{result['errors']} errors"
        ))
