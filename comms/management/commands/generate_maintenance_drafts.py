"""
Management command: generate portfolio-grain maintenance notes (Layer 1).

Usage:
    python manage.py generate_maintenance_drafts
    python manage.py generate_maintenance_drafts --portfolio-id 42
    python manage.py generate_maintenance_drafts --period-start 2026-05-25 --period-end 2026-05-31
    python manage.py generate_maintenance_drafts --dry-run
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand

from comms.services import generate_portfolio_maintenance_notes
from core.models import Portfolio


class Command(BaseCommand):
    help = "Generate portfolio-grain maintenance notes (Layer 1). No EmailDrafts created."

    def add_arguments(self, parser):
        parser.add_argument(
            "--portfolio-id",
            type=int,
            help="Generate for a single portfolio by ID.",
        )
        parser.add_argument(
            "--period-start",
            type=str,
            help="Period start date (YYYY-MM-DD). Defaults to last Monday.",
        )
        parser.add_argument(
            "--period-end",
            type=str,
            help="Period end date (YYYY-MM-DD). Defaults to last Sunday.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Limit the number of portfolios to process.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run the pipeline without writing to the database.",
        )

    def handle(self, *args, **options):
        period_start_str = options["period_start"]
        period_end_str = options["period_end"]

        # Default period: the most recent full week (Mon-Sun)
        today = date.today()
        if period_start_str:
            period_start = date.fromisoformat(period_start_str)
        else:
            # Last Monday
            period_start = today - timedelta(days=today.weekday() + 7)

        if period_end_str:
            period_end = date.fromisoformat(period_end_str)
        else:
            period_end = period_start + timedelta(days=6)

        self.stdout.write(
            f"Generating maintenance notes for {period_start} to {period_end}..."
        )

        portfolios = Portfolio.objects.filter(is_active=True)
        if options["portfolio_id"]:
            portfolios = portfolios.filter(pk=options["portfolio_id"])

        if options["limit"]:
            portfolios = portfolios[: options["limit"]]

        result = generate_portfolio_maintenance_notes(
            portfolios,
            period_start,
            period_end,
            period_type="weekly",
            dry_run=options["dry_run"],
        )

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
