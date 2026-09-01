"""
Build weekly UnitLeasingSnapshot rows for all active units.

Usage:
    python manage.py build_leasing_snapshots
    python manage.py build_leasing_snapshots --start 2026-08-25 --end 2026-08-31
    python manage.py build_leasing_snapshots --start 2026-08-25 --end 2026-08-31 --unit-id 42
    python manage.py build_leasing_snapshots --start 2026-08-25 --end 2026-08-31 --dry-run
    python manage.py build_leasing_snapshots --start 2026-08-24 --end 2026-08-31 --force
"""

import logging
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from core.models import Unit
from integrations.rentengine.client import RentEngineClient
from integrations.rentengine.mappers import (
    build_unit_applications,
    build_unit_marked_available_dates,
)
from integrations.rentvine.client import RentvineClient
from integrations.rentvine.listing_dates import build_unit_advertised_dates
from leasing.periods import resolve_period
from leasing.selectors import compute_leasing_metrics_bulk, compute_segment_benchmarks
from leasing.snapshots import build_unit_snapshot

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Build weekly UnitLeasingSnapshot rows for units with a rentengine_id."

    def add_arguments(self, parser):
        parser.add_argument(
            "--start",
            default=None,
            help="Period start date (YYYY-MM-DD), inclusive. Defaults to last complete Tue–Mon week.",
        )
        parser.add_argument(
            "--end",
            default=None,
            help="Period end date (YYYY-MM-DD), inclusive. Defaults to last complete Tue–Mon week.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Skip period validation (length and weekday checks). For intentional backfills.",
        )
        parser.add_argument(
            "--unit-id",
            type=int,
            default=None,
            help="Process a single core.Unit by ID.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute and print metrics without writing to the database.",
        )

    def handle(self, *args, **options):
        start, end = resolve_period(
            options["start"], options["end"], options["force"],
            stdout=self.stdout,
        )

        dry_run = options["dry_run"]
        single_unit_id = options["unit_id"]

        # --- Load units ---
        qs = Unit.objects.filter(rentengine_id__isnull=False).select_related(
            "property"
        )
        if single_unit_id is not None:
            qs = qs.filter(id=single_unit_id)

        units = list(qs.order_by("id"))
        if not units:
            raise CommandError("No units matched the filters.")

        self.stdout.write(
            f"Period: {start} to {end}  |  Units: {len(units)}  |  "
            f"Dry run: {dry_run}"
        )
        self.stdout.write("")

        # --- Pre-compute all local metrics in bulk (three DB queries) ---
        all_metrics = compute_leasing_metrics_bulk(units, start, end)

        # --- Close DB connection before API loop ---
        # Railway's Postgres drops idle connections during long API work.
        connection.close()

        client = RentEngineClient()

        # --- Fetch RentVine listing dates once for all units ---
        rv_advertised_dates = {}
        try:
            rv_client = RentvineClient()
            rv_listings = rv_client.get_all("properties/listings/search")
            rv_advertised_dates = build_unit_advertised_dates(rv_listings)
            self.stdout.write(
                f"RentVine listings: {len(rv_listings)} fetched, "
                f"{len(rv_advertised_dates)} active with advertisedDate"
            )
        except Exception as exc:
            self.stdout.write(
                self.style.WARNING(f"RentVine listing fetch failed: {exc}")
            )

        # --- Sanity check: how many units matched an advertised date? ---
        rv_unit_ids = {u.rentvine_id for u in units if u.rentvine_id is not None}
        matched_count = len(rv_unit_ids & set(rv_advertised_dates.keys()))
        unmatched_count = len(rv_unit_ids) - matched_count
        self.stdout.write(
            f"Advertised date match: {matched_count}/{len(rv_unit_ids)} units "
            f"matched, {unmatched_count} without"
        )
        if len(rv_unit_ids) > 0 and unmatched_count > len(rv_unit_ids) / 2:
            self.stdout.write(self.style.ERROR(
                "WARNING: More than half of units have no advertised date. "
                "The unitID join may be broken — days on market will be "
                "missing from most emails. Investigate before sending."
            ))

        # --- Fetch RentEngine /reporting/units once for all units ---
        re_marked_available_dates = {}
        span_days = (end - start).days
        if span_days > 32:
            self.stdout.write(self.style.WARNING(
                f"Period span is {span_days} days (max 32). Skipping "
                f"/reporting/units — date_marked_available will be null "
                f"and days on market will be omitted from emails."
            ))
        else:
            try:
                re_rows = client.get_reporting_units(start, end)
                re_marked_available_dates = (
                    build_unit_marked_available_dates(re_rows)
                )
                self.stdout.write(
                    f"RentEngine /reporting/units: {len(re_rows)} rows, "
                    f"{len(re_marked_available_dates)} with "
                    f"date_marked_available"
                )
            except Exception as exc:
                self.stdout.write(self.style.WARNING(
                    f"RentEngine /reporting/units fetch failed: {exc}"
                ))

            # Sanity check: how many units matched a date_marked_available?
            re_unit_ids = {
                u.rentengine_id for u in units
                if u.rentengine_id is not None
            }
            re_matched = len(
                re_unit_ids & set(re_marked_available_dates.keys())
            )
            re_unmatched = len(re_unit_ids) - re_matched
            self.stdout.write(
                f"date_marked_available match: {re_matched}/"
                f"{len(re_unit_ids)} units matched, "
                f"{re_unmatched} without"
            )
            if len(re_unit_ids) > 0 and re_unmatched > len(re_unit_ids) / 2:
                self.stdout.write(self.style.ERROR(
                    "WARNING: More than half of units have no "
                    "date_marked_available. The unit_id join may be "
                    "broken — days on market will be missing from most "
                    "emails. Investigate before sending."
                ))

        # --- Fetch application data once for all units ---
        unit_applications = {}
        try:
            app_rows = client.get_reporting_applications(start, end)
            group_rows = client.get_rental_application_groups()
            unit_applications = build_unit_applications(
                app_rows, group_rows, start, end,
            )
            total_entries = sum(len(v) for v in unit_applications.values())
            self.stdout.write(
                f"Applications: {len(app_rows)} reporting rows, "
                f"{len(group_rows)} groups, "
                f"{total_entries} entries across "
                f"{len(unit_applications)} units"
            )

            # Sanity check: how many of our units got application data?
            re_unit_ids = {
                u.rentengine_id for u in units
                if u.rentengine_id is not None
            }
            app_matched = len(
                re_unit_ids & set(unit_applications.keys())
            )
            self.stdout.write(
                f"Application match: {app_matched}/{len(re_unit_ids)} "
                f"units have applications"
            )
        except Exception as exc:
            self.stdout.write(self.style.ERROR(
                f"WARNING: Application data fetch failed: {exc}  "
                f"Applications will be MISSING from every email this run. "
                f"Investigate before sending."
            ))

        # --- Compute segment benchmarks once for all units ---
        benchmarks = compute_segment_benchmarks(end)

        # Build a map of ALL bedroom segments (qualified + suppressed) for reporting.
        # We need to know which segments exist even if suppressed, so re-check
        # what the function saw vs what it returned.
        all_br_counts = {}
        for u in units:
            if u.bedrooms is not None:
                all_br_counts.setdefault(u.bedrooms, set()).add(u.id)

        benchmark_parts = []
        for br in sorted(all_br_counts.keys()):
            count = len(all_br_counts[br])
            if br in benchmarks:
                benchmark_parts.append(f"{br}BR={count} units (qualified)")
            else:
                benchmark_parts.append(f"{br}BR={count} units (suppressed <5)")
        self.stdout.write(f"Benchmarks: {', '.join(benchmark_parts) or 'none'}")

        # --- Per-unit loop ---
        created_count = 0
        updated_count = 0
        fetch_failures = 0
        errors = []
        rows = []

        t0 = time.monotonic()

        for unit in units:
            m = all_metrics[unit.id]
            addr = unit.display_address or f"(unit {unit.id})"

            try:
                if dry_run:
                    # Still call reporting endpoint to show what we'd get
                    reporting_ok = False
                    calls = None
                    texts = None
                    upcoming = None
                    dom = None

                    if unit.rentengine_id is not None:
                        try:
                            rpt = client.get_unit_leasing_performance(
                                unit.rentengine_id, start, end,
                            )
                            if isinstance(rpt, dict) and "unit_id" in rpt:
                                reporting_ok = True
                                calls = rpt.get("total_calls")
                                texts = rpt.get("outbound_texts")
                                upcoming = rpt.get("upcoming_showings")
                                dom = rpt.get("days_on_market")
                        except Exception:
                            pass

                    rows.append((
                        unit.id, addr,
                        m["new_leads"], m["showings_scheduled"],
                        m["showings_completed"], m["showings_canceled"],
                        m["showings_missed"], m["applications_received"],
                        calls, texts, upcoming, dom, reporting_ok,
                    ))
                else:
                    snapshot, was_created = build_unit_snapshot(
                        unit, start, end, client=client,
                        rv_advertised_dates=rv_advertised_dates,
                        re_marked_available_dates=re_marked_available_dates,
                        applications=unit_applications.get(
                            unit.rentengine_id, [],
                        ),
                        segment_benchmark=benchmarks.get(
                            unit.bedrooms, {},
                        ) if unit.bedrooms is not None else {},
                    )
                    if was_created:
                        created_count += 1
                    else:
                        updated_count += 1

                    if not snapshot.reporting_fetch_ok:
                        fetch_failures += 1

                    rows.append((
                        unit.id, addr,
                        snapshot.new_leads, snapshot.showings_scheduled,
                        snapshot.showings_completed, snapshot.showings_canceled,
                        snapshot.showings_missed, snapshot.applications_received,
                        snapshot.touchpoints_calls, snapshot.touchpoints_texts,
                        snapshot.upcoming_showings, snapshot.days_on_market,
                        snapshot.reporting_fetch_ok,
                    ))

            except Exception as exc:
                errors.append((unit.id, addr, str(exc)))
                logger.warning(
                    "snapshot_unit_error",
                    extra={"unit_id": unit.id, "error": str(exc)},
                )

        elapsed = round(time.monotonic() - t0, 1)

        # --- Table output ---
        header = (
            f"{'ID':>5}  {'Address':<30}  {'Leads':>5}  {'Sched':>5}  "
            f"{'Compl':>5}  {'Cxl':>5}  {'Miss':>5}  {'Apps':>5}  "
            f"{'Calls':>5}  {'Texts':>5}  {'UpShw':>5}  {'DOM':>5}  "
            f"{'Rpt':>3}"
        )
        self.stdout.write(header)
        self.stdout.write("-" * len(header))

        for r in rows:
            uid, addr = r[0], r[1][:30]
            vals = r[2:]

            def fmt(v):
                if v is None:
                    return "    -"
                if isinstance(v, bool):
                    return "  ok" if v else " ERR"
                return f"{v:>5}"

            line = f"{uid:>5}  {addr:<30}  " + "  ".join(fmt(v) for v in vals)
            self.stdout.write(line)

        # --- Summary ---
        self.stdout.write("")
        self.stdout.write("=" * 60)
        if dry_run:
            self.stdout.write("DRY RUN — nothing written to the database.")
        else:
            self.stdout.write(f"  Snapshots created   : {created_count}")
            self.stdout.write(f"  Snapshots updated   : {updated_count}")
            self.stdout.write(f"  Reporting failures  : {fetch_failures}")
        self.stdout.write(f"  Units processed     : {len(rows)}")
        self.stdout.write(f"  Errors              : {len(errors)}")
        self.stdout.write(f"  Elapsed             : {elapsed}s")

        if errors:
            self.stdout.write("")
            self.stdout.write("ERRORS:")
            for uid, addr, err in errors:
                self.stdout.write(f"  Unit {uid} ({addr}): {err}")

        self.stdout.write("=" * 60)
