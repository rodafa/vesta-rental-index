"""
Leasing digest data layer — fetch and shape raw API data.

Pure fetch functions: call RentEngine / LeadSimple / Django ORM,
return dicts of raw rows. No business logic or formatting here.
"""

import logging
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from core.models import Unit
from integrations.leadsimple import client as ls_client
from integrations.leadsimple.client import DEGRADED
from integrations.rentengine.client import RentEngineClient
from leasing.models import ApplicationProcessCreation

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


def _iso_window(start_date: date, end_date: date) -> tuple[str, str]:
    """Convert ET date boundaries to UTC ISO-8601 strings.

    start_date 00:00:00 ET -> UTC ISO string
    end_date   23:59:59 ET -> UTC ISO string

    Handles DST transitions correctly via zoneinfo.
    """
    start_et = datetime(
        start_date.year, start_date.month, start_date.day,
        0, 0, 0, tzinfo=ET,
    )
    end_et = datetime(
        end_date.year, end_date.month, end_date.day,
        23, 59, 59, tzinfo=ET,
    )
    start_utc = start_et.astimezone(ZoneInfo("UTC"))
    end_utc = end_et.astimezone(ZoneInfo("UTC"))
    return (
        start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _log_fetch(endpoint: str, status: str, row_count: int, elapsed_ms: int):
    logger.info(
        "source_fetched",
        extra={
            "endpoint": endpoint,
            "status": status,
            "row_count": row_count,
            "elapsed_ms": elapsed_ms,
        },
    )


def fetch_daily_data(client: RentEngineClient, today: date) -> dict:
    """Fetch all data needed for the daily digest.

    Makes 5 RentEngine API calls:
      1. tasks (no date filter)
      2. reporting/showings (today, ET boundaries)
      3. reporting/overview (yesterday, ET boundaries)
      4. reporting/marketing-sources (yesterday, ET boundaries)
      5. property-performance (28-day trailing, for DOM count + address resolution)
    Plus N LeadSimple GETs for open application stages.
    """
    yesterday = today - timedelta(days=1)
    trailing_start = today - timedelta(days=27)

    today_start, today_end = _iso_window(today, today)
    yest_start, yest_end = _iso_window(yesterday, yesterday)
    trail_start, trail_end = _iso_window(trailing_start, today)

    data = {}

    # 1. Tasks
    t0 = time.monotonic()
    data["tasks"] = client.get_tasks()
    _log_fetch("tasks", "ok", len(data["tasks"]), int((time.monotonic() - t0) * 1000))

    # 2. Showings today
    t0 = time.monotonic()
    data["showings_today"] = client.get_reporting_showings(today_start, today_end)
    _log_fetch("showings", "ok", len(data["showings_today"]), int((time.monotonic() - t0) * 1000))

    # 3. Overview yesterday
    t0 = time.monotonic()
    data["overview_yesterday"] = client.get_reporting_overview(yest_start, yest_end)
    _log_fetch("overview", "ok", len(data["overview_yesterday"]), int((time.monotonic() - t0) * 1000))

    # 4. Marketing sources yesterday
    t0 = time.monotonic()
    data["marketing_sources_yesterday"] = client.get_reporting_marketing_sources(yest_start, yest_end)
    _log_fetch("marketing-sources", "ok", len(data["marketing_sources_yesterday"]), int((time.monotonic() - t0) * 1000))

    # 5. Property performance (28-day trailing)
    t0 = time.monotonic()
    data["property_performance"] = client.get_reporting_property_performance(trail_start, trail_end)
    _log_fetch("property-performance", "ok", len(data["property_performance"]), int((time.monotonic() - t0) * 1000))

    # 6. Application groups (all, no date filter)
    t0 = time.monotonic()
    data["application_groups"] = client.get_rental_application_groups()
    _log_fetch("application-groups", "ok", len(data["application_groups"]), int((time.monotonic() - t0) * 1000))

    # 7. LeadSimple process stages for open apps
    data["app_stages"] = _fetch_app_stages(data["application_groups"])

    # Build address lookup from property-performance
    data["address_lookup"] = _build_address_lookup(data["property_performance"])

    return data


def fetch_weekly_data(client: RentEngineClient, today: date, week_start: date, week_end: date) -> dict:
    """Fetch all data needed for the weekly digest.

    Makes ~11 RentEngine API calls:
      - property-performance (weekly window)
      - property-performance (28-day trailing)
      - showings (weekly window)
      - overview (weekly window)
      - overview (3 prior weeks for reply-time trend)
      - agent-performance (weekly window)
      - marketing-sources (weekly window)
      - applications (weekly window)
      - application-groups (all)
    """
    trailing_start = today - timedelta(days=27)

    week_start_iso, week_end_iso = _iso_window(week_start, week_end)
    trail_start_iso, trail_end_iso = _iso_window(trailing_start, today)

    data = {}

    # 1. Property performance — weekly window (scorecard)
    t0 = time.monotonic()
    data["pp_weekly"] = client.get_reporting_property_performance(week_start_iso, week_end_iso)
    _log_fetch("pp-weekly", "ok", len(data["pp_weekly"]), int((time.monotonic() - t0) * 1000))

    # 2. Property performance — 28-day trailing (watchlist)
    t0 = time.monotonic()
    data["pp_trailing"] = client.get_reporting_property_performance(trail_start_iso, trail_end_iso)
    _log_fetch("pp-trailing", "ok", len(data["pp_trailing"]), int((time.monotonic() - t0) * 1000))

    # 3. Showings — weekly window
    t0 = time.monotonic()
    data["showings"] = client.get_reporting_showings(week_start_iso, week_end_iso)
    _log_fetch("showings-weekly", "ok", len(data["showings"]), int((time.monotonic() - t0) * 1000))

    # 4. Overview — weekly window (reply time this week)
    t0 = time.monotonic()
    data["overview_weekly"] = client.get_reporting_overview(week_start_iso, week_end_iso)
    _log_fetch("overview-weekly", "ok", len(data["overview_weekly"]), int((time.monotonic() - t0) * 1000))

    # 5-7. Overview — prior 3 weeks for reply-time trend
    data["overview_history"] = []
    for i in range(1, 4):
        hist_end = week_start - timedelta(days=7 * (i - 1) + 1)
        hist_start = hist_end - timedelta(days=6)
        h_start_iso, h_end_iso = _iso_window(hist_start, hist_end)
        t0 = time.monotonic()
        rows = client.get_reporting_overview(h_start_iso, h_end_iso)
        _log_fetch(f"overview-history-{i}", "ok", len(rows), int((time.monotonic() - t0) * 1000))
        data["overview_history"].append({
            "start": hist_start,
            "end": hist_end,
            "rows": rows,
        })

    # 8. Marketing sources — weekly window
    t0 = time.monotonic()
    data["marketing_sources"] = client.get_reporting_marketing_sources(week_start_iso, week_end_iso)
    _log_fetch("marketing-sources-weekly", "ok", len(data["marketing_sources"]), int((time.monotonic() - t0) * 1000))

    # 9. Applications — weekly window
    t0 = time.monotonic()
    data["applications"] = client.get_reporting_applications(week_start, week_end)
    _log_fetch("applications-weekly", "ok", len(data["applications"]), int((time.monotonic() - t0) * 1000))

    # 10. Application groups (all, no date filter)
    t0 = time.monotonic()
    data["application_groups"] = client.get_rental_application_groups()
    _log_fetch("application-groups", "ok", len(data["application_groups"]), int((time.monotonic() - t0) * 1000))

    return data


def _fetch_app_stages(application_groups: list[dict]) -> dict:
    """Fetch LeadSimple stage for each open application group.

    Returns {group_id: stage_name_or_None}.
    """
    from automations.digest_config import APP_OPEN_STATUSES

    stages = {}
    open_groups = [
        g for g in application_groups
        if g.get("status") in APP_OPEN_STATUSES
    ]

    # Look up LeadSimple process IDs from our dedupe table
    group_ids = [g["id"] for g in open_groups]
    process_map = {}
    if group_ids:
        qs = ApplicationProcessCreation.objects.filter(
            rentengine_group_id__in=[str(gid) for gid in group_ids],
        ).values_list("rentengine_group_id", "leadsimple_process_id")
        process_map = {gid: pid for gid, pid in qs if pid}

    for group in open_groups:
        gid = str(group["id"])
        pid = process_map.get(gid)
        if not pid:
            stages[gid] = None
            continue

        result = ls_client.get_process(pid)
        if result is DEGRADED:
            stages[gid] = "stage unavailable"
            logger.warning(
                "source_fetched",
                extra={
                    "endpoint": f"leadsimple/processes/{pid}",
                    "status": "degraded",
                    "row_count": 0,
                    "elapsed_ms": 0,
                },
            )
        else:
            stage = (result.get("data") or result).get("stage") or {}
            stages[gid] = stage.get("name") or None

        # Pace LeadSimple requests
        time.sleep(1)

    return stages


def _build_address_lookup(pp_rows: list[dict]) -> dict:
    """Build {unit_id: address} from property-performance rows."""
    lookup = {}
    for row in pp_rows:
        uid = row.get("unit_id")
        addr = row.get("address", "")
        if uid and addr:
            lookup[uid] = addr
    return lookup


def resolve_address(unit_id: int, address_lookup: dict) -> str:
    """Resolve a unit's address from pp data, with ORM fallback."""
    if unit_id in address_lookup:
        return address_lookup[unit_id]
    unit = Unit.objects.filter(rentengine_id=unit_id).first()
    if unit:
        return unit.display_address
    return f"Unit {unit_id}"


