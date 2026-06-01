import json
import logging

from django.core.management.base import BaseCommand

from integrations.rentvine.client import RentvineClient
from integrations.rentvine.services import OwnerSyncService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sync owners from the Rentvine API"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and display sample data without writing to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write("=== DRY RUN: Fetching owners ===")
            client = RentvineClient()
            data = client.get("/owners/search", params={"page": 1, "pageSize": 5})
            self.stdout.write(json.dumps(data, indent=2, default=str))
            self.stdout.write("=== End dry run ===")
            return

        self.stdout.write("Syncing owners from Rentvine...")
        service = OwnerSyncService()
        result = service.sync(dry_run=False)
        self.stdout.write(self.style.SUCCESS(
            f"Owners synced: {result['fetched']} fetched, "
            f"{result['created']} created, {result['updated']} updated, "
            f"{result['errors']} errors"
        ))
