import csv
import logging
from collections import defaultdict
from datetime import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from leasing.models import LeasingEvent, Prospect
from market.models import DailyLeasingSummary
from properties.models import Unit

logger = logging.getLogger(__name__)

EVENT_TYPE_MAP = {
    "NEW_LEAD": "new",
    "SHOWING_COMPLETED": "showing_complete",
    "APPLICATION_RECEIVED": "application_received",
}


class Command(BaseCommand):
    help = "Import historical leasing events from CSV into Prospect, LeasingEvent, and DailyLeasingSummary"

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            type=str,
            default="data/historical_events.csv",
            help="Path to CSV file (default: data/historical_events.csv)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and validate without writing to DB",
        )

    def handle(self, *args, **options):
        csv_path = options["csv"]
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write("=== DRY RUN ===\n")

        # Read and validate CSV
        rows = self._read_csv(csv_path)
        if not rows:
            return

        # Build unit lookup
        unit_ids = {int(r["Unit ID"]) for r in rows}
        units = {u.rentengine_id: u for u in Unit.objects.filter(rentengine_id__in=unit_ids)}
        missing_units = unit_ids - set(units.keys())
        if missing_units:
            self.stdout.write(self.style.WARNING(
                f"WARNING: {len(missing_units)} Unit IDs not found: {sorted(missing_units)[:10]}..."
            ))

        # Step 1: Create Prospects
        prospect_data = self._build_prospect_data(rows, units)
        self.stdout.write(f"\nProspects to create/update: {len(prospect_data)}")

        if not dry_run:
            prospects_created = self._create_prospects(prospect_data, units)
        else:
            prospects_created = len(prospect_data)

        # Build prospect lookup for event creation
        prospect_ids = {int(r["Prospect ID"]) for r in rows}
        if not dry_run:
            prospect_map = {
                p.rentengine_id: p
                for p in Prospect.objects.filter(rentengine_id__in=prospect_ids)
            }
        else:
            prospect_map = {}

        # Step 2: Create LeasingEvents
        events = self._build_events(rows, units, prospect_map, dry_run)
        self.stdout.write(f"LeasingEvents to create: {len(events)}")

        events_created = 0
        if not dry_run and events:
            LeasingEvent.objects.bulk_create(events, batch_size=500)
            events_created = len(events)

        # Step 3: Compute DailyLeasingSummary
        daily_counts = self._compute_daily_counts(rows)
        self.stdout.write(f"DailyLeasingSummary rows to create/update: {len(daily_counts)}")

        summaries_created = 0
        summaries_updated = 0
        if not dry_run:
            summaries_created, summaries_updated = self._create_daily_summaries(
                daily_counts, units
            )

        # Summary
        self.stdout.write(f"\n{'='*50}")
        if dry_run:
            self.stdout.write("DRY RUN SUMMARY:")
            self.stdout.write(f"  CSV rows parsed: {len(rows)}")
            self.stdout.write(f"  Unique prospects: {len(prospect_data)}")
            self.stdout.write(f"  Events to create: {len(events) if events else len(rows)}")
            self.stdout.write(f"  Daily summaries to create: {len(daily_counts)}")
            self.stdout.write(f"  Units matched: {len(unit_ids) - len(missing_units)}/{len(unit_ids)}")
        else:
            self.stdout.write(self.style.SUCCESS("IMPORT COMPLETE:"))
            self.stdout.write(f"  Prospects created/updated: {prospects_created}")
            self.stdout.write(f"  LeasingEvents created: {events_created}")
            self.stdout.write(f"  DailyLeasingSummary created: {summaries_created}, updated: {summaries_updated}")

        self.stdout.write(
            "\nNext: run  aggregate_market_data --backfill --start 2025-11-17 --end 2026-03-02  "
            "to generate weekly/monthly summaries."
        )

    def _read_csv(self, path):
        try:
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        except FileNotFoundError:
            self.stdout.write(self.style.ERROR(f"CSV file not found: {path}"))
            return []

        if not rows:
            self.stdout.write(self.style.ERROR("CSV file is empty"))
            return []

        self.stdout.write(f"Read {len(rows)} rows from {path}")

        # Validate event types
        bad_types = set()
        for r in rows:
            if r["EventType"] not in EVENT_TYPE_MAP:
                bad_types.add(r["EventType"])
        if bad_types:
            self.stdout.write(self.style.ERROR(f"Unknown event types: {bad_types}"))
            return []

        return rows

    def _build_prospect_data(self, rows, units):
        """Group rows by Prospect ID, find earliest timestamp and first unit."""
        prospects = {}
        for r in rows:
            pid = int(r["Prospect ID"])
            uid = int(r["Unit ID"])
            ts = self._parse_timestamp(r["Timestamp"])
            if pid not in prospects:
                prospects[pid] = {"unit_id": uid, "earliest_ts": ts}
            else:
                if ts < prospects[pid]["earliest_ts"]:
                    prospects[pid]["earliest_ts"] = ts
        return prospects

    def _create_prospects(self, prospect_data, units):
        created = 0
        for pid, data in prospect_data.items():
            unit = units.get(data["unit_id"])
            _, was_created = Prospect.objects.update_or_create(
                rentengine_id=pid,
                defaults={
                    "name": f"Prospect {pid}",
                    "unit_of_interest": unit,
                    "source_created_at": data["earliest_ts"],
                    "status": "active",
                },
            )
            if was_created:
                created += 1
        return created

    def _build_events(self, rows, units, prospect_map, dry_run):
        if dry_run:
            return []

        events = []
        for r in rows:
            uid = int(r["Unit ID"])
            pid = int(r["Prospect ID"])
            unit = units.get(uid)
            prospect = prospect_map.get(pid)

            if not unit:
                continue

            events.append(LeasingEvent(
                prospect=prospect,
                unit=unit,
                event_type=EVENT_TYPE_MAP[r["EventType"]],
                event_timestamp=self._parse_timestamp(r["Timestamp"]),
                event_date=datetime.strptime(r["EventDate"], "%Y-%m-%d").date(),
                raw_data={
                    "source": "historical_csv",
                    "Timestamp": r["Timestamp"],
                    "EventDate": r["EventDate"],
                    "EventType": r["EventType"],
                    "Prospect ID": r["Prospect ID"],
                    "Unit ID": r["Unit ID"],
                },
            ))
        return events

    def _compute_daily_counts(self, rows):
        """Group by (event_date, unit_id) and count by type."""
        counts = defaultdict(lambda: {"new": 0, "showing_complete": 0, "application_received": 0})
        for r in rows:
            key = (r["EventDate"], int(r["Unit ID"]))
            event_type = EVENT_TYPE_MAP[r["EventType"]]
            counts[key][event_type] += 1
        return counts

    def _create_daily_summaries(self, daily_counts, units):
        created = 0
        updated = 0
        for (date_str, unit_id), type_counts in daily_counts.items():
            unit = units.get(unit_id)
            if not unit:
                continue

            summary_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            _, was_created = DailyLeasingSummary.objects.update_or_create(
                summary_date=summary_date,
                unit=unit,
                defaults={
                    "leads_count": type_counts["new"],
                    "showings_completed_count": type_counts["showing_complete"],
                    "showings_missed_count": 0,
                    "applications_count": type_counts["application_received"],
                    "property_display_name": str(unit.property),
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1
        return created, updated

    def _parse_timestamp(self, ts_str):
        """Parse '11/20/2025 11:54:01' into timezone-aware datetime."""
        naive = datetime.strptime(ts_str, "%m/%d/%Y %H:%M:%S")
        return timezone.make_aware(naive)
