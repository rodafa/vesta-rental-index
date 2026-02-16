import json
import logging

from django.core.management.base import BaseCommand

from integrations.rentengine.client import RentEngineClient
from integrations.rentengine.services import UnitSyncService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sync units from the RentEngine API and create DailyUnitSnapshots"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and display sample data without writing to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write("=== DRY RUN: Fetching RentEngine units ===")
            client = RentEngineClient()
            data = client.get("/units", params={"limit": 5, "page_number": 0})
            self.stdout.write(json.dumps(data, indent=2, default=str))
            self.stdout.write("=== End dry run ===")
            return

        self.stdout.write("Syncing units from RentEngine...")
        service = UnitSyncService()
        result = service.sync(dry_run=False)
        self.stdout.write(self.style.SUCCESS(
            f"Units synced: {result['fetched']} fetched, "
            f"{result['created']} created, {result['updated']} updated, "
            f"{result['snapshots']} snapshots, {result['errors']} errors"
        ))
