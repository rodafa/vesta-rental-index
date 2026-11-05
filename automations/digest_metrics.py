"""
Leasing digest metrics — pure functions that turn shaped data into numbers/tags.

No API calls, no formatting. Input: dicts from digest_data. Output: numbers, lists, tags.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from automations.digest_config import (
    APP_OPEN_STATUSES,
    APP_STALE_HOURS,
    DOM_THRESHOLD,
    PLACEHOLDER_RE,
    SHOWING_COMPLETE_STATUSES,
    TEAM_PASS_THRESHOLD,
    WEEKLY_GOALS,
)

ET = ZoneInfo("America/New_York")


def _get_address(row: dict) -> str:
    """Get address from a row, handling both pp and showings field names."""
    return row.get("property_address") or row.get("address") or ""


def is_placeholder(address: str) -> bool:
    """True if the address is a placeholder (e.g. contains 'applications only')."""
    return bool(PLACEHOLDER_RE.search(address or ""))


def _available_rows(pp_rows: list[dict]) -> list[dict]:
    """Filter to Available, non-placeholder rows."""
    return [
        r for r in pp_rows
        if r.get("unit_status") == "Available"
        and not is_placeholder(r.get("address", ""))
    ]


def compute_dom(dma_str: str | None, today: date) -> int | None:
    """Compute days on market from date_marked_available string.

    Converts DMA to Eastern Time date before subtracting, so a timestamp
    at e.g. 02:25 UTC (previous evening ET) gives the correct ET date.
    """
    if not dma_str:
        return None
    try:
        dma_utc = datetime.fromisoformat(dma_str)
        dma_et_date = dma_utc.astimezone(ET).date()
        return (today - dma_et_date).days
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Scorecard measurables
# ---------------------------------------------------------------------------

def compute_units_leased(pp_rows: list[dict], week_start: date, week_end: date) -> int:
    """Count units with status 'Leased' and date_marked_unavailable in [week_start, week_end]."""
    seen = set()
    count = 0
    for row in pp_rows:
        if is_placeholder(row.get("address", "")):
            continue
        if row.get("unit_status") != "Leased":
            continue
        uid = row.get("unit_id")
        if uid in seen:
            continue
        dmu = row.get("date_marked_unavailable")
        if not dmu:
            continue
        try:
            dmu_date = datetime.fromisoformat(dmu).astimezone(ET).date()
        except (ValueError, TypeError):
            continue
        if week_start <= dmu_date <= week_end:
            seen.add(uid)
            count += 1
    return count


def compute_apps_submitted(application_groups: list[dict], week_start: date, week_end: date) -> int:
    """Count unique application groups submitted within the week window."""
    seen = set()
    count = 0
    for group in application_groups:
        gid = group.get("id")
        if gid in seen:
            continue
        submitted = group.get("submitted_at")
        if not submitted:
            continue
        try:
            sub_date = datetime.fromisoformat(submitted).astimezone(ET).date()
        except (ValueError, TypeError):
            continue
        if week_start <= sub_date <= week_end:
            seen.add(gid)
            count += 1
    return count


def compute_showing_completion(showings: list[dict]) -> dict:
    """Compute showing completion rate.

    Returns {total, complete, rate_pct, adjusted_total, adjusted_rate_pct}.
    """
    total = len([s for s in showings if not is_placeholder(_get_address(s))])
    complete = len([
        s for s in showings
        if s.get("status") in SHOWING_COMPLETE_STATUSES
        and not is_placeholder(_get_address(s))
    ])
    excluded = len([
        s for s in showings
        if (s.get("is_rescheduled") or s.get("is_auto_canceled"))
        and not is_placeholder(_get_address(s))
    ])
    adjusted_total = total - excluded

    return {
        "total": total,
        "complete": complete,
        "rate_pct": round(complete / total * 100) if total else None,
        "adjusted_total": adjusted_total,
        "adjusted_rate_pct": round(complete / adjusted_total * 100) if adjusted_total else None,
    }


def compute_lead_to_showing(marketing_rows: list[dict]) -> int | None:
    """Compute lead-to-showing conversion rate from marketing sources.

    Sum leads and showings_scheduled across all sources.
    Rate = showings_scheduled / leads.
    """
    total_leads = sum(r.get("leads", 0) for r in marketing_rows)
    total_scheduled = sum(r.get("showings_scheduled", 0) for r in marketing_rows)
    if total_leads == 0:
        return None
    return round(total_scheduled / total_leads * 100)


def compute_call_answer_rate(overview) -> int | None:
    """Compute call answer rate from overview data.

    Overview is a single dict with answered_inbound_calls, total_inbound_calls,
    and call_answer_rate (pre-computed decimal).
    """
    if isinstance(overview, dict):
        rate = overview.get("call_answer_rate")
        if rate is not None:
            return round(rate * 100)
        return None
    return None


def compute_reply_time(overview) -> float | None:
    """Extract reply time in minutes from overview data.

    Overview is a single dict from /reporting/overview, or a list
    of dicts for test compatibility. Uses sms_response_seconds.
    """
    if isinstance(overview, dict):
        val = overview.get("sms_response_seconds")
        if val is not None:
            return round(val / 60, 1)
        return None
    # List input (for tests or batched data)
    values = [
        r["sms_response_seconds"]
        for r in overview
        if isinstance(r, dict) and r.get("sms_response_seconds") is not None
    ]
    if not values:
        return None
    avg_seconds = sum(values) / len(values)
    return round(avg_seconds / 60, 1)


def compute_feedback_captured(showings: list[dict]) -> dict:
    """Compute feedback capture rate for completed showings.

    Returns {complete_count, with_feedback, rate_pct}.
    Feedback is the 'feedback' free-text field on each showing.
    """
    complete = [
        s for s in showings
        if s.get("status") in SHOWING_COMPLETE_STATUSES
        and not is_placeholder(_get_address(s))
    ]
    with_feedback = sum(1 for s in complete if s.get("feedback"))

    return {
        "complete_count": len(complete),
        "with_feedback": with_feedback,
        "rate_pct": round(with_feedback / len(complete) * 100) if complete else None,
    }


def compute_stale_apps(application_groups: list[dict], now: datetime) -> dict:
    """Count application groups open > 72 hours.

    Returns {open_groups: list[dict], stale_count: int}.
    """
    utc = ZoneInfo("UTC")
    open_groups = []
    for group in application_groups:
        status = group.get("status")
        if status not in APP_OPEN_STATUSES:
            continue
        submitted = group.get("submitted_at")
        if not submitted:
            continue
        try:
            sub_dt = datetime.fromisoformat(submitted)
            if sub_dt.tzinfo is None:
                sub_dt = sub_dt.replace(tzinfo=utc)
        except (ValueError, TypeError):
            continue
        hours = (now - sub_dt).total_seconds() / 3600
        open_groups.append({
            **group,
            "_hours_open": hours,
            "_is_stale": hours > APP_STALE_HOURS,
        })

    stale_count = sum(1 for g in open_groups if g["_is_stale"])
    # Sort by hours open descending (oldest first)
    open_groups.sort(key=lambda g: g["_hours_open"], reverse=True)

    return {"open_groups": open_groups, "stale_count": stale_count}


def compute_units_past_dom(pp_rows: list[dict], today: date) -> dict:
    """Count Available units with DOM >= threshold.

    Returns {count: int, units: list[dict]}.
    Each unit dict is the original row plus a "dom" key with the computed value.
    Units are sorted by DOM descending.
    """
    rows = _available_rows(pp_rows)
    units = []
    for row in rows:
        if row.get("excluded_from_dom"):
            continue
        dom = compute_dom(row.get("date_marked_available"), today)
        if dom is None:
            continue
        if dom >= DOM_THRESHOLD:
            units.append({**row, "dom": dom})

    units.sort(key=lambda u: u["dom"], reverse=True)
    return {"count": len(units), "units": units}


# ---------------------------------------------------------------------------
# Scorecard scoring
# ---------------------------------------------------------------------------

def score_measurables(metrics: dict) -> dict:
    """Score all graded measurables against goals.

    Returns {
        results: [(key, label, value, goal_display, is_green: bool), ...],
        green_count: int,
        total_graded: int,
        team_pass: bool,
    }
    """
    results = []
    green_count = 0

    for key, label, goal, direction, goal_display in WEEKLY_GOALS:
        value = metrics.get(key)
        if value is None:
            is_green = False
        elif direction == "gte":
            is_green = value >= goal
        else:  # lte
            is_green = value <= goal

        if is_green:
            green_count += 1
        results.append((key, label, value, goal_display, is_green))

    total_graded = len(WEEKLY_GOALS)
    return {
        "results": results,
        "green_count": green_count,
        "total_graded": total_graded,
        "team_pass": green_count >= TEAM_PASS_THRESHOLD,
    }


# ---------------------------------------------------------------------------
# Weekly helpers (moved from command)
# ---------------------------------------------------------------------------


def compute_week_window(today: date) -> tuple[date, date]:
    """Compute Mon-Sun week window for the weekly digest.

    If today is Monday, report last week. If Sunday, that's the week ending.
    Otherwise, find previous Sunday.

    Returns (week_start, week_end) where week_start is Monday and week_end is Sunday.
    """
    if today.weekday() == 0:  # Monday
        week_end = today - timedelta(days=1)
    elif today.weekday() == 6:  # Sunday
        week_end = today
    else:
        week_end = today - timedelta(days=today.weekday() + 1)
    week_start = week_end - timedelta(days=6)
    return week_start, week_end


def compute_reply_time_history(overview_history: list[dict]) -> list[dict]:
    """Build reply-time trend from prior-week overview data.

    Each entry in overview_history has keys: rows (overview dict), start (date), end (date).
    Returns list of {value: float|None, label: str}.
    """
    result = []
    for entry in overview_history:
        val = compute_reply_time(entry["rows"])
        s, e = entry["start"], entry["end"]
        result.append({
            "value": val,
            "label": f"{s.strftime('%b')} {s.day}\u2013{e.day}",
        })
    return result


# ---------------------------------------------------------------------------
# Daily helpers
# ---------------------------------------------------------------------------

def compute_daily_tasks(tasks: list[dict]) -> list[dict]:
    """Categorize open tasks for the daily needs-action section."""
    categories = {}
    for task in tasks:
        task_type = task.get("task_type") or task.get("type") or "Unknown"
        created = task.get("created_at") or task.get("date_created")
        categories.setdefault(task_type, []).append({
            "task": task,
            "created_at": created,
        })
    return categories


def compute_yesterday_leads(marketing_rows: list[dict]) -> int:
    """Sum leads across all marketing source rows."""
    return sum(r.get("leads", 0) for r in marketing_rows)


def compute_daily_showings(showings: list[dict], today: date) -> list[dict]:
    """Process and sort today's showings by time ascending.

    Returns list of {time_et, address, agent, status_label, status_emoji}.
    """
    from automations.digest_config import SHOWING_STATUS_DEFAULT, SHOWING_STATUS_MAP

    result = []
    for s in showings:
        addr = _get_address(s)
        if is_placeholder(addr):
            continue
        status = s.get("status", "")
        label, emoji = SHOWING_STATUS_MAP.get(status, SHOWING_STATUS_DEFAULT)

        # Parse time to ET
        raw_time = s.get("planned_date_time") or ""
        time_et = ""
        if raw_time:
            try:
                dt = datetime.fromisoformat(raw_time)
                dt_et = dt.astimezone(ET)
                time_et = f"{dt_et.hour % 12 or 12}:{dt_et.minute:02d} {'AM' if dt_et.hour < 12 else 'PM'}"
            except (ValueError, TypeError):
                time_et = raw_time

        result.append({
            "time_et": time_et,
            "time_sort": raw_time,
            "address": addr,
            "agent": s.get("showing_agent") or "",
            "status_label": label,
            "status_emoji": emoji,
        })

    # Sort by time ascending
    result.sort(key=lambda x: x["time_sort"])
    return result
