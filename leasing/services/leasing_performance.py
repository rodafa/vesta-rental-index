"""
Leasing Directory service layer.

Pure functions, Celery-ready (no implicit globals).
Fetches daily leasing performance from RentEngine, upserts into
DailyLeasingMetric and ShowingFeedback, and handles CSV backfill.
"""

import csv
import logging
import os
from datetime import date, datetime, timedelta, timezone as dt_tz
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils.dateparse import parse_datetime

from integrations.rentengine.client import RentEngineClient
from leasing.models import DailyLeasingMetric, ShowingFeedback
from leasing.services.leasing_deltas import recompute_deltas_for_unit
from properties.models import Unit

logger = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Column-name aliases for flexible CSV parsing
# ---------------------------------------------------------------------------

_COLUMN_MAP = {
    "date": "date",
    "unit_id": "unit_id",
    "unit id": "unit_id",
    "unitid": "unit_id",
    "rentengine_unit_id": "unit_id",
    "rentengine unit id": "unit_id",
    "re_unit_id": "unit_id",
    "property_address": "address",
    "property address": "address",
    "address": "address",
    "new_prospects": "new_prospects",
    "new prospects": "new_prospects",
    "leads": "new_prospects",
    "newleads": "new_prospects",
    "showings_completed": "showings_completed",
    "showings completed": "showings_completed",
    "showingscompleted": "showings_completed",
    "completed_showings": "showings_completed",
    "applications_submitted": "applications_submitted",
    "applications submitted": "applications_submitted",
    "applications": "applications_submitted",
    "apps received": "applications_submitted",
    "apps_received": "applications_submitted",
    "appsreceived": "applications_submitted",
    "showings_missed_or_failed": "showings_missed_or_failed",
    "showings missed or failed": "showings_missed_or_failed",
    "showings_missed/failed": "showings_missed_or_failed",
    "showings missed/failed": "showings_missed_or_failed",
    "missed/failed": "showings_missed_or_failed",
    "missed_showings": "showings_missed_or_failed",
    "missed showings": "showings_missed_or_failed",
}


def _normalize_headers(headers):
    """Map raw CSV headers to canonical field names."""
    mapping = {}
    for raw in headers:
        key = raw.strip().lower()
        canonical = _COLUMN_MAP.get(key)
        if canonical:
            mapping[raw] = canonical
    return mapping


# ---------------------------------------------------------------------------
# API fetch
# ---------------------------------------------------------------------------


def fetch_daily_performance(unit_id: int, day: date) -> dict:
    """
    Fetch one unit's leasing performance for a single day from RentEngine.

    Uses America/New_York midnight-to-midnight boundaries converted to UTC
    for the API call, so events near midnight ET are assigned to the correct
    calendar day.
    The RentEngineClient handles 429 retries with Retry-After.
    """
    client = RentEngineClient()
    local_start = datetime(day.year, day.month, day.day, tzinfo=EASTERN)
    local_end = local_start + timedelta(days=1) - timedelta(seconds=1)
    start_utc = local_start.astimezone(dt_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_utc = local_end.astimezone(dt_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return client.get(
        f"/reporting/leasing-performance/units/{unit_id}",
        params={"start": start_utc, "end": end_utc},
    )


# ---------------------------------------------------------------------------
# Upsert helpers
# ---------------------------------------------------------------------------


def upsert_daily_metric(unit, day, payload):
    """
    Idempotent upsert of a DailyLeasingMetric row for (unit, day).

    Computes showings_missed_or_failed = max(0, scheduled - completed - upcoming).
    Copies payload['showing_feedback'] into showing_feedback_summary with
    field-name normalisation (API ``created_at`` -> ``showing_completed_at``).
    Stores the full API response as raw_payload.

    Returns (DailyLeasingMetric, created: bool).
    """
    scheduled = payload.get("showings_scheduled", 0) or 0
    completed = payload.get("showings_completed", 0) or 0
    upcoming = payload.get("upcoming_showings", 0) or 0
    missed = max(0, scheduled - completed - upcoming)

    raw_feedback = payload.get("showing_feedback") or []
    feedback_summary = [
        {
            "prospect_id": fb.get("prospect_id"),
            "prospect_name": fb.get("prospect_name", ""),
            "showing_completed_at": fb.get("created_at", ""),
            "feedback": fb.get("feedback"),
        }
        for fb in raw_feedback
    ]

    defaults = {
        "new_prospects": payload.get("new_prospects", 0) or 0,
        "showings_completed": completed,
        "showings_missed_or_failed": missed,
        "days_on_market": payload.get("days_on_market"),
        "property_health": payload.get("property_health"),
        "benchmark_leads_since_last_price_change": payload.get(
            "benchmark_leads_since_last_price_change"
        ),
        "showings_scheduled": scheduled,
        "applications_requested": payload.get("applications_requested"),
        "active_prospects": payload.get("active_prospects"),
        "upcoming_showings": upcoming,
        "outbound_texts": payload.get("outbound_texts"),
        "total_calls": payload.get("total_calls"),
        "showing_feedback_summary": feedback_summary,
        "source": "rentengine_api",
        "raw_payload": payload,
    }

    metric, created = DailyLeasingMetric.objects.update_or_create(
        unit=unit,
        date=day,
        defaults=defaults,
    )
    return metric, created


def upsert_showing_feedback(unit, feedback_array):
    """
    Upsert ShowingFeedback rows from the API's showing_feedback array.

    Returns count of rows upserted (created or updated).
    """
    count = 0
    for fb in feedback_array:
        prospect_id = fb.get("prospect_id")
        raw_dt = fb.get("created_at")
        if not prospect_id or not raw_dt:
            continue

        completed_at = parse_datetime(raw_dt)
        if not completed_at:
            continue

        ShowingFeedback.objects.update_or_create(
            rentengine_prospect_id=prospect_id,
            showing_completed_at=completed_at,
            defaults={
                "unit": unit,
                "prospect_name": fb.get("prospect_name", ""),
                "feedback": fb.get("feedback"),
            },
        )
        count += 1

    return count


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def ingest_day(day, unit_ids=None):
    """
    Ingest leasing performance for all active units on a given day.

    When unit_ids is None, defaults to Unit records that have rentengine_id
    set AND had status="active" on that day's DailyUnitSnapshot
    (via the ``daily_snapshots`` reverse relation).

    When unit_ids is provided, they are treated as rentengine_ids.

    Each unit's upsert_daily_metric + upsert_showing_feedback is wrapped
    in a single transaction.atomic() so the denormalized summary and
    canonical feedback stay in sync.

    Returns {'units_processed', 'metrics_upserted', 'feedback_upserted',
             'errors': [...]}.
    """
    if unit_ids is not None:
        units = Unit.objects.filter(rentengine_id__in=unit_ids)
    else:
        units = Unit.objects.filter(
            rentengine_id__isnull=False,
            daily_snapshots__snapshot_date=day,
            daily_snapshots__status="active",
        ).distinct()

    result = {
        "units_processed": 0,
        "metrics_upserted": 0,
        "feedback_upserted": 0,
        "errors": [],
    }

    for unit in units:
        try:
            payload = fetch_daily_performance(unit.rentengine_id, day)

            with transaction.atomic():
                _, _ = upsert_daily_metric(unit, day, payload)
                feedback_count = upsert_showing_feedback(
                    unit, payload.get("showing_feedback") or []
                )

            recompute_deltas_for_unit(unit, from_date=day)

            result["units_processed"] += 1
            result["metrics_upserted"] += 1
            result["feedback_upserted"] += feedback_count
        except Exception as exc:
            msg = f"Unit {unit.rentengine_id}: {exc}"
            logger.error("ingest_day error: %s", msg)
            result["errors"].append(msg)

    return result


# ---------------------------------------------------------------------------
# CSV backfill
# ---------------------------------------------------------------------------


def import_historical_csv(csv_path, dry_run=False):
    """
    Import historical daily leasing metrics from a CSV file.

    For each unique rentengine_unit_id, resolves via
    Unit.objects.filter(rentengine_id=...). Matched units get
    DailyLeasingMetric rows with source='csv_backfill' and
    showing_feedback_summary=[].

    Unmatched units are collected and written to unmatched_units.txt
    in the same directory as csv_path.

    Rows where the address column starts with 'http' are still processed
    (the address is simply ignored — Unit matching is by rentengine_id).

    Returns {'rows_imported', 'units_matched', 'units_unmatched',
             'unmatched_unit_ids': [...]}.
    """
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header_map = _normalize_headers(reader.fieldnames or [])

        # Build reverse lookup: canonical -> raw header name
        reverse = {}
        for raw, canonical in header_map.items():
            reverse[canonical] = raw

        if "unit_id" not in reverse:
            raise ValueError(
                f"CSV missing unit ID column. Headers: {reader.fieldnames}"
            )
        if "date" not in reverse:
            raise ValueError(
                f"CSV missing date column. Headers: {reader.fieldnames}"
            )

        rows = list(reader)

    def _int(val):
        try:
            return int(val.strip()) if val and val.strip() else 0
        except (ValueError, AttributeError):
            return 0

    # Cache unit lookups
    unit_cache = {}  # rentengine_id -> Unit | None
    matched_ids = set()
    unmatched_ids = set()
    rows_imported = 0

    for row in rows:
        re_id_raw = row.get(reverse["unit_id"], "").strip()
        if not re_id_raw:
            continue

        try:
            re_id = int(re_id_raw)
        except ValueError:
            continue

        if re_id not in unit_cache:
            unit_cache[re_id] = Unit.objects.filter(
                rentengine_id=re_id
            ).first()

        unit = unit_cache[re_id]
        if unit is None:
            unmatched_ids.add(re_id)
            continue

        matched_ids.add(re_id)

        date_str = row.get(reverse["date"], "").strip()
        if not date_str:
            continue

        try:
            row_date = date.fromisoformat(date_str)
        except ValueError:
            # Try common US format MM/DD/YYYY
            try:
                parts = date_str.split("/")
                row_date = date(int(parts[2]), int(parts[0]), int(parts[1]))
            except (ValueError, IndexError):
                logger.warning(
                    "Skipping row with unparseable date: %s", date_str
                )
                continue

        defaults = {
            "new_prospects": _int(
                row.get(reverse.get("new_prospects", ""), "")
            ),
            "showings_completed": _int(
                row.get(reverse.get("showings_completed", ""), "")
            ),
            "applications_submitted": _int(
                row.get(reverse.get("applications_submitted", ""), "")
            ),
            "showings_missed_or_failed": _int(
                row.get(reverse.get("showings_missed_or_failed", ""), "")
            ),
            "showing_feedback_summary": [],
            "source": "csv_backfill",
        }

        if dry_run:
            logger.info(
                "DRY RUN: unit %d, date %s, %s", re_id, row_date, defaults
            )
        else:
            DailyLeasingMetric.objects.update_or_create(
                unit=unit,
                date=row_date,
                defaults=defaults,
            )

        rows_imported += 1

    # Write unmatched units file
    if unmatched_ids and not dry_run:
        out_dir = os.path.dirname(os.path.abspath(csv_path))
        out_path = os.path.join(out_dir, "unmatched_units.txt")
        with open(out_path, "w") as f:
            for uid in sorted(unmatched_ids):
                f.write(f"{uid}\n")
        logger.info(
            "Wrote %d unmatched unit IDs to %s", len(unmatched_ids), out_path
        )

    return {
        "rows_imported": rows_imported,
        "units_matched": len(matched_ids),
        "units_unmatched": len(unmatched_ids),
        "unmatched_unit_ids": sorted(unmatched_ids),
    }
