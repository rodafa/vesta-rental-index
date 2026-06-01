import logging

from django.core.management.base import BaseCommand

from integrations.rentengine.services import ShowingFeedbackSyncService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sync historical showing feedback from RentEngine into Showing records"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and log without writing to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write("=== DRY RUN: Fetching RentEngine showing feedback ===")

        self.stdout.write("Syncing showing feedback from RentEngine...")
        service = ShowingFeedbackSyncService()
        result = service.sync(dry_run=dry_run)

        if dry_run:
            self.stdout.write("=== End dry run ===")
            return

        self.stdout.write(self.style.SUCCESS(
            f"Showing feedback synced: {result['fetched']} total showings scanned, "
            f"{result['created']} created, {result['updated']} updated, "
            f"{result['errors']} errors"
        ))
