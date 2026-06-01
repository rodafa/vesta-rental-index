import json
import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from integrations.boompay.client import BoompayAPIError, BoompayClient
from integrations.boompay.services import ApplicationSyncService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sync screening applications from the BoomPay/BoomScreen API"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and display sample data without writing to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write("=== DRY RUN: Fetching screening applications ===")
            base_url = settings.BOOMPAY.get("BASE_URL", "")
            self.stdout.write(f"BASE_URL: {base_url}")
            try:
                client = BoompayClient()
                data = client.get("/applications", params={"page": 1, "page_size": 5})
                self.stdout.write(json.dumps(data, indent=2, default=str))
            except BoompayAPIError as exc:
                self.stdout.write(self.style.WARNING(f"API error: {exc}"))
                if exc.response_body:
                    self.stdout.write(f"Response body (first 500 chars):\n{exc.response_body[:500]}")
            self.stdout.write("=== End dry run ===")
            return

        self.stdout.write("Syncing screening applications from BoomScreen...")
        service = ApplicationSyncService()
        result = service.sync(dry_run=False)
        self.stdout.write(self.style.SUCCESS(
            f"Applications synced: {result['fetched']} fetched, "
            f"{result['created']} created, {result['updated']} updated, "
            f"{result['errors']} errors"
        ))
