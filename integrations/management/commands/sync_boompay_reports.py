import json
import logging

from django.core.management.base import BaseCommand

from integrations.boompay.client import BoompayClient
from integrations.boompay.services import ReportSyncService
from screening.models import ScreeningApplication

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sync screening reports from the BoomPay/BoomScreen API (iterates all synced applications)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and display sample data without writing to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write("=== DRY RUN: Fetching screening reports ===")
            client = BoompayClient()
            app = ScreeningApplication.objects.filter(
                boompay_id__isnull=False,
            ).exclude(boompay_id="").first()
            if app:
                self.stdout.write(
                    f"Fetching reports for application {app.boompay_id}..."
                )
                data = client.get(
                    f"/applications/{app.boompay_id}/reports",
                    params={"page": 1, "page_size": 5},
                )
                self.stdout.write(json.dumps(data, indent=2, default=str))
            else:
                self.stdout.write(
                    "No synced screening applications found. "
                    "Sync applications first."
                )
            self.stdout.write("=== End dry run ===")
            return

        self.stdout.write("Syncing screening reports from BoomScreen...")
        service = ReportSyncService()
        result = service.sync(dry_run=False)
        self.stdout.write(self.style.SUCCESS(
            f"Reports synced: {result['fetched']} fetched, "
            f"{result['created']} created, {result['updated']} updated, "
            f"{result['errors']} errors"
        ))
