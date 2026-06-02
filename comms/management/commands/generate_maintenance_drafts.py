"""
Management command: generate maintenance email drafts.

Usage:
    python manage.py generate_maintenance_drafts
    python manage.py generate_maintenance_drafts --owner-id 42
    python manage.py generate_maintenance_drafts --week-start 2026-05-25 --week-end 2026-05-31
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand

from comms.services import generate_drafts
from core.models import Owner


class Command(BaseCommand):
    help = "Generate maintenance email drafts for owners."

    def add_arguments(self, parser):
        parser.add_argument(
            "--owner-id",
            type=int,
            help="Generate for a single owner by ID.",
        )
        parser.add_argument(
            "--week-start",
            type=str,
            help="Week start date (YYYY-MM-DD). Defaults to last Monday.",
        )
        parser.add_argument(
            "--week-end",
            type=str,
            help="Week end date (YYYY-MM-DD). Defaults to last Sunday.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Limit the number of owners to process.",
        )

    def handle(self, *args, **options):
        # Default week range: the most recent full week (Mon-Sun)
        today = date.today()
        if options["week_start"]:
            week_start = date.fromisoformat(options["week_start"])
        else:
            # Last Monday
            week_start = today - timedelta(days=today.weekday() + 7)

        if options["week_end"]:
            week_end = date.fromisoformat(options["week_end"])
        else:
            week_end = week_start + timedelta(days=6)

        self.stdout.write(
            f"Generating maintenance drafts for {week_start} to {week_end}..."
        )

        owners = Owner.objects.filter(is_active=True)
        if options["owner_id"]:
            owners = owners.filter(pk=options["owner_id"])

        owners = owners.prefetch_related("portfolios")

        if options["limit"]:
            owners = owners[: options["limit"]]

        result = generate_drafts("maintenance", owners, week_start, week_end)

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: generated={result['generated']}  "
                f"skipped={result['skipped']}  "
                f"errors={result['errors']}"
            )
        )
        if result["error_details"]:
            for err in result["error_details"]:
                self.stderr.write(f"  {err}")
