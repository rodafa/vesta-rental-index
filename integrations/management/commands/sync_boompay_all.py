import logging
import time

from django.core.management.base import BaseCommand

from integrations.boompay.client import BoompayClient
from integrations.boompay.services import (
    ApplicationSyncService,
    ReportSyncService,
)

logger = logging.getLogger(__name__)

SYNC_ORDER = [
    ("applications", ApplicationSyncService),
    ("reports", ReportSyncService),
]


class Command(BaseCommand):
    help = "Sync all BoomScreen entities in dependency order: applications -> reports"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and display sample data without writing to the database",
        )
        parser.add_argument(
            "--skip",
            nargs="+",
            choices=["applications", "reports"],
            default=[],
            help="Skip specific entity types (e.g., --skip applications)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        skip = set(options["skip"])

        client = BoompayClient()
        overall_start = time.time()
        results = {}

        for name, service_class in SYNC_ORDER:
            if name in skip:
                self.stdout.write(f"Skipping {name}")
                continue

            self.stdout.write(f"\n{'='*50}")
            self.stdout.write(f"Syncing {name}...")
            self.stdout.write(f"{'='*50}")

            start = time.time()
            service = service_class(client=client)

            if dry_run:
                self.stdout.write(
                    f"DRY RUN for {name} - calling individual command for sample data"
                )
                from django.core.management import call_command
                call_command(
                    f"sync_boompay_{name}", dry_run=True, stdout=self.stdout
                )
                continue

            try:
                result = service.sync(dry_run=False)
                elapsed = time.time() - start
                results[name] = result
                self.stdout.write(self.style.SUCCESS(
                    f"{name}: {result['fetched']} fetched, "
                    f"{result['created']} created, {result['updated']} updated, "
                    f"{result['errors']} errors ({elapsed:.1f}s)"
                ))
            except Exception as exc:
                elapsed = time.time() - start
                self.stdout.write(self.style.ERROR(
                    f"{name}: FAILED after {elapsed:.1f}s - {exc}"
                ))
                results[name] = {"error": str(exc)}

        total_elapsed = time.time() - overall_start
        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(f"Sync complete in {total_elapsed:.1f}s")
        self.stdout.write(f"{'='*50}")

        for name, result in results.items():
            if "error" in result:
                self.stdout.write(self.style.ERROR(
                    f"  {name}: FAILED - {result['error']}"
                ))
            else:
                self.stdout.write(
                    f"  {name}: {result['created']} created, "
                    f"{result['updated']} updated, {result['errors']} errors"
                )
