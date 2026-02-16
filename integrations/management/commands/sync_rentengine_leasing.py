import json
import logging

from django.core.management.base import BaseCommand

from integrations.rentengine.client import RentEngineClient
from integrations.rentengine.services import LeasingPerformanceSyncService
from properties.models import Unit

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sync leasing performance from RentEngine into DailyLeasingSummary"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and display sample data without writing to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write("=== DRY RUN: Fetching RentEngine leasing performance ===")
            client = RentEngineClient()
            unit = Unit.objects.filter(rentengine_id__isnull=False).first()
            if unit:
                from django.utils import timezone

                today = timezone.now().date().isoformat()
                self.stdout.write(
                    f"Fetching leasing performance for unit {unit.rentengine_id}..."
                )
                data = client.get(
                    f"/reporting/leasing-performance/units/{unit.rentengine_id}",
                    params={
                        "start": f"{today}T00:00:00Z",
                        "end": f"{today}T23:59:59Z",
                    },
                )
                self.stdout.write(json.dumps(data, indent=2, default=str))
            else:
                self.stdout.write(
                    "No units with rentengine_id found. Sync units first."
                )
            self.stdout.write("=== End dry run ===")
            return

        self.stdout.write("Syncing leasing performance from RentEngine...")
        service = LeasingPerformanceSyncService()
        result = service.sync(dry_run=False)
        self.stdout.write(self.style.SUCCESS(
            f"Leasing synced: {result['fetched']} fetched, "
            f"{result['created']} created, {result['updated']} updated, "
            f"{result['errors']} errors"
        ))
