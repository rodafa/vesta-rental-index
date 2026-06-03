"""
Management command: generate monthly owner notes email drafts.

Usage:
    python manage.py generate_monthly_drafts
    python manage.py generate_monthly_drafts --owner-id 42
    python manage.py generate_monthly_drafts --period-start 2026-05-01 --period-end 2026-05-31
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand

from comms.services import generate_drafts
from core.models import Owner


class Command(BaseCommand):
    help = "Generate monthly owner notes email drafts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--owner-id",
            type=int,
            help="Generate for a single owner by ID.",
        )
        parser.add_argument(
            "--period-start",
            type=str,
            help="Period start date (YYYY-MM-DD). Defaults to first of previous month.",
        )
        parser.add_argument(
            "--period-end",
            type=str,
            help="Period end date (YYYY-MM-DD). Defaults to last of previous month.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Limit the number of owners to process.",
        )

    def handle(self, *args, **options):
        period_start_str = options["period_start"]
        period_end_str = options["period_end"]

        # Default period: the previous calendar month
        today = date.today()
        if period_start_str:
            period_start = date.fromisoformat(period_start_str)
        else:
            first_of_this_month = today.replace(day=1)
            # Last day of previous month
            last_of_prev = first_of_this_month - timedelta(days=1)
            period_start = last_of_prev.replace(day=1)

        if period_end_str:
            period_end = date.fromisoformat(period_end_str)
        else:
            # Last day of the period_start's month
            if period_start.month == 12:
                period_end = period_start.replace(month=12, day=31)
            else:
                next_month_first = period_start.replace(
                    month=period_start.month + 1, day=1
                )
                period_end = next_month_first - timedelta(days=1)

        self.stdout.write(
            f"Generating monthly owner notes for {period_start} to {period_end}..."
        )

        owners = Owner.objects.filter(is_active=True)
        if options["owner_id"]:
            owners = owners.filter(pk=options["owner_id"])

        owners = owners.prefetch_related("portfolios")

        if options["limit"]:
            owners = owners[: options["limit"]]

        result = generate_drafts(
            "monthly_owner_notes", owners, period_start, period_end,
            period_type="monthly",
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: generated={result['generated']}  "
                f"skipped={result['skipped']}  "
                f"degraded={result['degraded']}  "
                f"errors={result['errors']}"
            )
        )
        if result["error_details"]:
            for err in result["error_details"]:
                self.stderr.write(f"  {err}")
