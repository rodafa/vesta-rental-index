"""
Import historical daily leasing metrics from a CSV file.

Usage:
    python manage.py import_leasing_history --csv path/to/file.csv [--dry-run]
"""

from django.core.management.base import BaseCommand

from leasing.services.leasing_performance import import_historical_csv


class Command(BaseCommand):
    help = "Import historical leasing metrics from a CSV file into DailyLeasingMetric."

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            required=True,
            help="Path to the CSV file to import.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and log without writing to the database.",
        )

    def handle(self, *args, **options):
        csv_path = options["csv"]
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write("DRY RUN — no data will be written.\n")

        result = import_historical_csv(csv_path, dry_run=dry_run)

        self.stdout.write(
            f"Rows imported: {result['rows_imported']}\n"
            f"Units matched: {result['units_matched']}\n"
            f"Units unmatched: {result['units_unmatched']}\n"
        )

        if result["unmatched_unit_ids"]:
            self.stdout.write(
                f"Unmatched unit IDs: {result['unmatched_unit_ids']}\n"
            )
            if not dry_run:
                self.stdout.write("Wrote unmatched IDs to unmatched_units.txt\n")
