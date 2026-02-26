import logging
import time
from datetime import date, timedelta

from django.core.management.base import BaseCommand

from dashboard.models import OwnerReportNote
from market.models import DailyUnitSnapshot
from properties.models import Owner

logger = logging.getLogger(__name__)


def _week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


class Command(BaseCommand):
    help = (
        "Generate draft owner report notes for all owners with active listings. "
        "Intended to run Monday night so the team can review Tuesday morning."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            help="Report week Monday date (YYYY-MM-DD). Default: this week's Monday.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created without writing to the database.",
        )

    def handle(self, *args, **options):
        start_time = time.time()

        if options["date"]:
            from datetime import datetime
            report_date = datetime.strptime(options["date"], "%Y-%m-%d").date()
        else:
            report_date = _week_monday(date.today())

        dry_run = options["dry_run"]

        self.stdout.write(f"\nGenerating weekly owner reports for {report_date}")
        self.stdout.write(f"{'='*50}")

        # Find owners with active listings (same logic as owners_with_active_listings endpoint)
        latest_date = (
            DailyUnitSnapshot.objects.order_by("-snapshot_date")
            .values_list("snapshot_date", flat=True)
            .first()
        )
        if not latest_date:
            self.stdout.write("No snapshots found — nothing to generate.")
            return

        active_unit_ids = set(
            DailyUnitSnapshot.objects.filter(
                snapshot_date=latest_date, status="active"
            ).values_list("unit_id", flat=True)
        )

        owners = Owner.objects.filter(is_active=True).prefetch_related(
            "portfolios__properties__units"
        )

        created = 0
        skipped = 0
        errors = 0

        for owner in owners:
            # Count active listings for this owner
            listing_count = 0
            for portfolio in owner.portfolios.all():
                for prop in portfolio.properties.filter(is_active=True):
                    for unit in prop.units.filter(is_active=True):
                        if unit.id in active_unit_ids:
                            listing_count += 1

            if listing_count == 0:
                continue

            # Check if a note already exists for this owner+date
            existing = OwnerReportNote.objects.filter(
                owner=owner, report_date=report_date
            ).first()

            if existing:
                self.stdout.write(
                    f"  {owner.name}: already has {existing.status} note — skipping"
                )
                skipped += 1
                continue

            subject = (
                f"Weekly Listing Update — {report_date.strftime('%b %d, %Y')}"
            )

            if dry_run:
                self.stdout.write(
                    f"  [DRY RUN] {owner.name}: would create draft "
                    f"({listing_count} active listings)"
                )
                created += 1
                continue

            try:
                OwnerReportNote.objects.create(
                    owner=owner,
                    report_date=report_date,
                    status="draft",
                    email_subject=subject,
                    notes_text="",
                    email_body="",
                )
                created += 1
                self.stdout.write(self.style.SUCCESS(
                    f"  {owner.name}: draft created ({listing_count} active listings)"
                ))
            except Exception as exc:
                errors += 1
                self.stdout.write(self.style.ERROR(
                    f"  {owner.name}: FAILED — {exc}"
                ))
                logger.exception(
                    "Failed to create report note for owner %s", owner.name
                )

        elapsed = time.time() - start_time
        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(
            f"Done in {elapsed:.1f}s — "
            f"{created} created, {skipped} skipped, {errors} errors"
        )
