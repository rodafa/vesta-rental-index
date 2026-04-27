"""
Management command: generate_monthly_owner_notes

Generates AI-drafted monthly owner notes for manual review and pasting
into RentVine's owner notes field.

Usage examples:
    # Dry-run for last month (no DB writes)
    python manage.py generate_monthly_owner_notes --dry-run

    # Single owner, specific month
    python manage.py generate_monthly_owner_notes --owner-id 12345 --month 2026-03

    # Single property by Django PK
    python manage.py generate_monthly_owner_notes --property-id 42 --dry-run
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from reports.services.monthly_owner_report import run_monthly_report


class Command(BaseCommand):
    help = "Generate AI-drafted monthly owner notes for pasting into RentVine."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print notes to stdout without writing to the database.",
        )
        parser.add_argument(
            "--owner-id",
            type=str,
            default=None,
            help="RentVine contact ID of a single owner to process.",
        )
        parser.add_argument(
            "--property-id",
            type=int,
            default=None,
            help="Run only the portfolio containing this property PK.",
        )
        parser.add_argument(
            "--month",
            type=str,
            default=None,
            help=(
                "Month to report on in YYYY-MM format. "
                "Defaults to the previous calendar month."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        owner_id = options["owner_id"]
        property_id = options["property_id"]
        month_str = options["month"]

        # Resolve month
        if month_str:
            try:
                month = date.fromisoformat(f"{month_str}-01")
            except ValueError:
                raise CommandError(
                    f"Invalid --month value '{month_str}'. Use YYYY-MM format, e.g. 2026-03"
                )
        else:
            today = date.today()
            if today.month == 1:
                month = date(today.year - 1, 12, 1)
            else:
                month = date(today.year, today.month - 1, 1)

        if dry_run:
            self.stdout.write(
                self.style.WARNING("DRY RUN — notes will be printed but not saved to the database.")
            )

        self.stdout.write(
            f"Generating monthly owner notes for {month:%B %Y} …"
        )

        try:
            result = run_monthly_report(
                month=month,
                owner_id=owner_id,
                property_id=property_id,
                dry_run=dry_run,
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        summary = (
            f"\nDone. "
            f"Generated: {result['generated']}  "
            f"Skipped: {result['skipped']}  "
            f"Failed: {result['failed']}"
        )
        self.stdout.write(self.style.SUCCESS(summary))
