import json
import logging

from django.core.management.base import BaseCommand

from integrations.rentvine.client import RentvineClient
from integrations.rentvine.services import UnitSyncService
from properties.models import Property

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sync units from the Rentvine API (iterates all synced properties)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and display sample data without writing to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write("=== DRY RUN: Fetching units ===")
            client = RentvineClient()
            # Show units for the first synced property
            prop = Property.objects.filter(rentvine_id__isnull=False).first()
            if prop:
                self.stdout.write(f"Fetching units for property {prop.rentvine_id}...")
                data = client.get(
                    f"/properties/{prop.rentvine_id}/units",
                    params={"page": 1, "pageSize": 5},
                )
                self.stdout.write(json.dumps(data, indent=2, default=str))
            else:
                self.stdout.write("No synced properties found. Sync properties first.")
            self.stdout.write("=== End dry run ===")
            return

        self.stdout.write("Syncing units from Rentvine...")
        service = UnitSyncService()
        result = service.sync(dry_run=False)
        self.stdout.write(self.style.SUCCESS(
            f"Units synced: {result['fetched']} fetched, "
            f"{result['created']} created, {result['updated']} updated, "
            f"{result['errors']} errors"
        ))
