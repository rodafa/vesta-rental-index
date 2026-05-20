"""
Management command: draft_property_notes

Generates AI-drafted PropertyWeeklyNote records for all actively-listed units.

Usage:
    python manage.py draft_property_notes [--date YYYY-MM-DD] [--dry-run]
"""
import logging
from datetime import date, timedelta

from django.core.management.base import BaseCommand

from dashboard.draft_notes import draft_all_property_notes

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "AI-draft PropertyWeeklyNote records for all actively-listed units."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Monday of the target week as YYYY-MM-DD (default: this Monday)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Build prompts and print drafts without saving to the database.",
        )

    def handle(self, *args, **options):
        raw_date = options.get("date")
        dry_run = options.get("dry_run", False)

        if raw_date:
            try:
                week_date = date.fromisoformat(raw_date)
            except ValueError:
                self.stderr.write(f"Invalid date: {raw_date!r}. Use YYYY-MM-DD.")
                return
        else:
            today = date.today()
            week_date = today - timedelta(days=today.weekday())  # most recent Monday

        self.stdout.write(f"Drafting property notes for week of {week_date} (dry_run={dry_run})")

        result = draft_all_property_notes(week_date=week_date, dry_run=dry_run)

        self.stdout.write(
            f"Done — drafted: {result['drafted']}, skipped: {result['skipped']}"
        )
        if dry_run and result.get("previews"):
            for preview in result["previews"]:
                self.stdout.write(f"\n--- Unit #{preview['unit_id']} ({preview['address']}) ---")
                self.stdout.write(preview["draft"])
