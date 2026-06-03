"""
Management command: sync RentVine bills and bill charges.

Runs BillSyncService (bills → links to melds) then BillChargeSyncService
(type-7 transactions → links to bills) in sequence.

Usage:
    python manage.py sync_rentvine_bills
    python manage.py sync_rentvine_bills --days 90
    python manage.py sync_rentvine_bills --dry-run
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand

from integrations.rentvine.services import BillChargeSyncService, BillSyncService


class Command(BaseCommand):
    help = "Sync bills and bill charges from the RentVine API."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="Only sync records from the last N days (default: 90). "
            "Pass 0 to sync all history.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and log records without writing to the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        days = options["days"]

        from_date = None
        if days > 0:
            from_date = date.today() - timedelta(days=days)
            self.stdout.write(f"Filtering to records from {from_date} onward ({days} days).")
        else:
            self.stdout.write("Syncing full history (no date filter).")

        # --- Bills ---
        self.stdout.write("Syncing bills from RentVine...")
        bill_result = BillSyncService().sync(dry_run=dry_run, from_date=from_date)
        self.stdout.write(
            self.style.SUCCESS(
                f"Bills: fetched={bill_result['fetched']}  "
                f"created={bill_result['created']}  "
                f"updated={bill_result['updated']}  "
                f"linked={bill_result['linked']}  "
                f"unlinked={bill_result['unlinked']}  "
                f"no_ref={bill_result['no_ref']}  "
                f"errors={bill_result['errors']}"
            )
        )

        # --- Bill charges ---
        self.stdout.write("Syncing bill charges from RentVine...")
        charge_result = BillChargeSyncService().sync(
            dry_run=dry_run, from_date=from_date
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Charges: fetched={charge_result['fetched']}  "
                f"created={charge_result['created']}  "
                f"updated={charge_result['updated']}  "
                f"orphaned={charge_result['orphaned']}  "
                f"errors={charge_result['errors']}"
            )
        )
