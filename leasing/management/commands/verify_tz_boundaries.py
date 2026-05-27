"""
Compare RentEngine leasing-performance results using UTC vs US/Eastern day boundaries.

Determines which timezone boundary produces the correct lead counts
(matching RentEngine's own PDF reports).

Usage:
    python manage.py verify_tz_boundaries \
        --address "Fortunate Drive" \
        --start 2026-05-19 --end 2026-05-25

    python manage.py verify_tz_boundaries \
        --unit-id 12345 \
        --start 2026-05-19 --end 2026-05-25
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError

from integrations.rentengine.client import RentEngineClient
from properties.models import Unit

UTC = timezone.utc
EASTERN = ZoneInfo("US/Eastern")


def _fetch_new_prospects(client, rentengine_id, start_utc_str, end_utc_str):
    """Fetch new_prospects count for one unit and time window."""
    data = client.get(
        f"/reporting/leasing-performance/units/{rentengine_id}",
        params={"start": start_utc_str, "end": end_utc_str},
    )
    return data.get("new_prospects", 0)


class Command(BaseCommand):
    help = (
        "Compare RentEngine lead counts using UTC vs US/Eastern day boundaries "
        "to identify which timezone produces correct totals."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--address",
            type=str,
            default=None,
            help="Partial address to look up the unit (case-insensitive contains).",
        )
        parser.add_argument(
            "--unit-id",
            type=int,
            default=None,
            help="RentEngine unit ID (skips address lookup).",
        )
        parser.add_argument(
            "--start",
            type=str,
            required=True,
            help="Start date (YYYY-MM-DD), inclusive.",
        )
        parser.add_argument(
            "--end",
            type=str,
            required=True,
            help="End date (YYYY-MM-DD), inclusive.",
        )

    def handle(self, *args, **options):
        rentengine_id = options["unit_id"]

        if not rentengine_id:
            if not options["address"]:
                raise CommandError("Provide --address or --unit-id.")
            units = Unit.objects.filter(
                address_line_1__icontains=options["address"],
                rentengine_id__isnull=False,
            )
            if not units.exists():
                raise CommandError(
                    f"No unit found matching address '{options['address']}' "
                    "with a rentengine_id."
                )
            if units.count() > 1:
                self.stderr.write("Multiple units matched:\n")
                for u in units:
                    self.stderr.write(
                        f"  ID {u.rentengine_id}: {u.address_line_1} {u.address_line_2}\n"
                    )
                raise CommandError(
                    "Narrow the --address filter or use --unit-id directly."
                )
            unit = units.first()
            rentengine_id = unit.rentengine_id
            self.stdout.write(
                f"Matched unit: {unit.address_line_1} {unit.address_line_2} "
                f"(rentengine_id={rentengine_id})\n\n"
            )

        start_date = date.fromisoformat(options["start"])
        end_date = date.fromisoformat(options["end"])
        if start_date > end_date:
            raise CommandError("--start must be <= --end.")

        client = RentEngineClient()

        # Header
        self.stdout.write(
            f"{'Date':<12} {'UTC leads':>10} {'Eastern leads':>14}\n"
        )
        self.stdout.write("-" * 38 + "\n")

        utc_total = 0
        eastern_total = 0
        current = start_date

        while current <= end_date:
            # UTC boundaries: day 00:00:00Z to day 23:59:59Z
            utc_start = f"{current.isoformat()}T00:00:00Z"
            utc_end = f"{current.isoformat()}T23:59:59Z"

            # Eastern boundaries: local midnight to local 23:59:59, converted to UTC
            local_start = datetime(
                current.year, current.month, current.day, tzinfo=EASTERN
            )
            local_end = local_start + timedelta(days=1) - timedelta(seconds=1)
            eastern_start_utc = local_start.astimezone(UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            eastern_end_utc = local_end.astimezone(UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

            utc_leads = _fetch_new_prospects(
                client, rentengine_id, utc_start, utc_end
            )
            eastern_leads = _fetch_new_prospects(
                client, rentengine_id, eastern_start_utc, eastern_end_utc
            )

            utc_total += utc_leads
            eastern_total += eastern_leads

            self.stdout.write(
                f"{current.isoformat():<12} {utc_leads:>10} {eastern_leads:>14}\n"
            )

            current += timedelta(days=1)

        # Totals
        self.stdout.write("-" * 38 + "\n")
        self.stdout.write(
            f"{'TOTAL':<12} {utc_total:>10} {eastern_total:>14}\n"
        )
        self.stdout.write(
            f"\nExpected total from RentEngine PDF: 8\n"
            f"UTC match: {'YES' if utc_total == 8 else 'NO'}\n"
            f"Eastern match: {'YES' if eastern_total == 8 else 'NO'}\n"
        )
