import logging
from datetime import date

from django.core.management.base import BaseCommand

from integrations.boompay.services import ApplicationCountService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Populate DailyLeasingSummary.applications_count from "
        "BoomScreen ScreeningApplication records"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Log what would change without writing to the database",
        )
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Target date in YYYY-MM-DD format (default: today)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        target_date = None
        if options["date"]:
            target_date = date.fromisoformat(options["date"])

        if dry_run:
            self.stdout.write("=== DRY RUN: Application count sync ===")

        service = ApplicationCountService()
        result = service.sync(target_date=target_date, dry_run=dry_run)

        self.stdout.write(self.style.SUCCESS(
            f"Application counts: "
            f"{result['created']} created, {result['updated']} updated, "
            f"{result['errors']} errors"
        ))
