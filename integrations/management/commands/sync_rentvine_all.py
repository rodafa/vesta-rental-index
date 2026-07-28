import logging
import time

from django.core.management.base import BaseCommand

from integrations.rentvine.client import RentvineClient
from integrations.rentvine.services import (
    BillChargeSyncService,
    BillSyncService,
    LeaseSyncService,
    OwnerSyncService,
    PortfolioSyncService,
    PropertySyncService,
    UnitSyncService,
    WorkOrderSyncService,
    link_owners_from_portfolio_contacts,
)

logger = logging.getLogger(__name__)

SYNC_ORDER = [
    ("portfolios", PortfolioSyncService),
    ("owners", OwnerSyncService),
    ("properties", PropertySyncService),
    ("units", UnitSyncService),
    ("leases", LeaseSyncService),
    ("work_orders", WorkOrderSyncService),
]


class Command(BaseCommand):
    help = "Sync all entities from Rentvine in dependency order: portfolios -> owners -> properties -> units -> leases -> work_orders -> bills"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and display sample data without writing to the database",
        )
        parser.add_argument(
            "--skip",
            nargs="+",
            choices=["portfolios", "owners", "properties", "units", "leases", "work_orders", "bills"],
            default=[],
            help="Skip specific entity types (e.g., --skip portfolios owners)",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Only sync bills from the last N days (default: 30). "
            "Pass 0 to sync all bill history. Only affects bills.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        skip = set(options["skip"])
        bill_days = options["days"]

        client = RentvineClient()
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
                self.stdout.write(f"DRY RUN for {name} - calling individual command for sample data")
                from django.core.management import call_command
                call_command(f"sync_rentvine_{name}", dry_run=True, stdout=self.stdout)
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

        # ── Bills (special case: two services + from_date filter) ────
        if "bills" not in skip:
            self.stdout.write(f"\n{'='*50}")
            self.stdout.write("Syncing bills...")
            self.stdout.write(f"{'='*50}")

            if dry_run:
                self.stdout.write("DRY RUN for bills - calling individual command for sample data")
                from django.core.management import call_command
                call_command("sync_rentvine_bills", dry_run=True, stdout=self.stdout)
            else:
                from datetime import date, timedelta

                from_date = None
                if bill_days > 0:
                    from_date = date.today() - timedelta(days=bill_days)
                    self.stdout.write(f"Filtering bills to last {bill_days} days (from {from_date}).")

                start = time.time()
                try:
                    bill_svc = BillSyncService(client=client)
                    bill_result = bill_svc.sync(dry_run=False, from_date=from_date)
                    charge_svc = BillChargeSyncService(client=client)
                    charge_result = charge_svc.sync(dry_run=False, from_date=from_date)
                    elapsed = time.time() - start
                    results["bills"] = bill_result
                    results["bill_charges"] = charge_result
                    self.stdout.write(self.style.SUCCESS(
                        f"bills: {bill_result['fetched']} fetched, "
                        f"{bill_result['created']} created, {bill_result['updated']} updated, "
                        f"{bill_result['errors']} errors ({elapsed:.1f}s)"
                    ))
                    self.stdout.write(self.style.SUCCESS(
                        f"bill_charges: {charge_result['fetched']} fetched, "
                        f"{charge_result['created']} created, {charge_result['updated']} updated, "
                        f"{charge_result['errors']} errors"
                    ))
                except Exception as exc:
                    elapsed = time.time() - start
                    self.stdout.write(self.style.ERROR(
                        f"bills: FAILED after {elapsed:.1f}s - {exc}"
                    ))
                    results["bills"] = {"error": str(exc)}

        # Final owner-portfolio link pass (idempotent safety net)
        if not dry_run and "portfolios" not in skip:
            self.stdout.write(f"\n{'='*50}")
            self.stdout.write("Linking owners to portfolios...")
            self.stdout.write(f"{'='*50}")
            link_result = link_owners_from_portfolio_contacts()
            self.stdout.write(self.style.SUCCESS(
                f"Owner-portfolio links: {link_result['linked']} linked, "
                f"{link_result['skipped']} skipped"
            ))

        total_elapsed = time.time() - overall_start
        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(f"Sync complete in {total_elapsed:.1f}s")
        self.stdout.write(f"{'='*50}")

        for name, result in results.items():
            if "error" in result:
                self.stdout.write(self.style.ERROR(f"  {name}: FAILED - {result['error']}"))
            else:
                self.stdout.write(
                    f"  {name}: {result['created']} created, "
                    f"{result['updated']} updated, {result['errors']} errors"
                )
