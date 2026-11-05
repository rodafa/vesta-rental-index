"""
Leasing digest metrics — pure functions that turn shaped data into numbers/tags.

No API calls, no formatting. Input: dicts from digest_data. Output: numbers, lists, tags.
"""

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from automations.digest_config import (
    APP_OPEN_STATUSES,
    APP_STALE_HOURS,
    CONVERT_APPS_MAX,
    CONVERT_SHOWINGS_MIN,
    DOM_THRESHOLD,
    FIX_KEYWORD_FIELDS,
    FIX_KEYWORD_RE,
    FOLLOW_UP_LEADS_THRESHOLD,
    FOLLOW_UP_SHOWINGS_MAX,
    LOW_LEADS_LPW_THRESHOLD,
    PLACEHOLDER_RE,
    SHOWING_COMPLETE_STATUSES,
    TAG_CONVERT,
    TAG_FIX,
    TAG_FOLLOW_UP,
    TAG_LOW_LEADS,
    TAG_PRICE,
    TAG_REVIEW,
    TAG_WAIT,
    TEAM_PASS_THRESHOLD,
    WATCHLIST_DOM_TRIGGER,
    WATCHLIST_KEYWORD_HIT_THRESHOLD,
    WATCHLIST_LPW_TRIGGER,
    WATCHLIST_LPW_WINDOW_DAYS,
    WATCHLIST_MIN_DOM_FOR_LEAD_RULE,
    WEEKLY_GOALS,
)
from automations.digest_data import parse_showing_complete_context

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
    DOM is computed by converting date_marked_available to ET date first.
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
            units.append({**row, "_dom": dom})

    units.sort(key=lambda u: u["_dom"], reverse=True)
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


def compute_agent_contributions(agent_rows: list[dict]) -> list[dict]:
    """Extract per-agent contributions, excluding Unassigned and all-zero agents.

    Returns list of {name, showings, messages, calls, approved}.
    """
    result = []
    for row in agent_rows:
        name = (row.get("agent_name") or "").strip()
        if name.lower() == "unassigned":
            continue
        entry = {
            "name": name,
            "showings": row.get("showings_completed", 0),
            "messages": row.get("messages_sent", 0),
            "calls": row.get("calls_made", 0),
            "approved": row.get("apps_approved", 0),
        }
        if entry["showings"] == 0 and entry["messages"] == 0 and entry["calls"] == 0 and entry["approved"] == 0:
            continue
        result.append(entry)
    return result


def compute_top_sources(marketing_rows: list[dict], top_n: int = 3) -> list[dict]:
    """Top N lead sources by lead count.

    Returns list of {source, leads, scheduled, approved}.
    """
    sorted_rows = sorted(
        marketing_rows, key=lambda r: r.get("leads", 0), reverse=True,
    )[:top_n]
    return [
        {
            "source": s.get("source_display", "Unknown"),
            "leads": s.get("leads", 0),
            "scheduled": s.get("showings_scheduled", 0),
            "approved": s.get("apps_approved", 0),
        }
        for s in sorted_rows
    ]


def compute_issues(scorecard: dict) -> list[tuple]:
    """Extract red measurables as issues for IDS discussion.

    Returns list of (label, display_value, goal_display).
    """
    pct_keys = {"showing_completion", "lead_to_showing", "call_answer_rate", "feedback_captured"}
    issues = []
    for key, label, value, goal_display, is_green in scorecard["results"]:
        if not is_green:
            display = f"{value}%" if key in pct_keys else value
            issues.append((label, display, goal_display))
    return issues


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

def _count_keyword_hits(row: dict) -> int:
    """Count showing_complete_context entries with condition keyword matches."""
    raw_context = row.get("showing_complete_context")
    entries = parse_showing_complete_context(
        raw_context if isinstance(raw_context, list) else [raw_context] if raw_context else []
    )

    hit_count = 0
    for entry in entries:
        for field in FIX_KEYWORD_FIELDS:
            text = entry.get(field, "")
            if text and FIX_KEYWORD_RE.search(str(text)):
                hit_count += 1
                break  # count each entry at most once
    return hit_count


def _compute_lpw(leads: int, dom: int) -> float:
    """Compute leads per week normalized by exposure."""
    weeks = min(dom, WATCHLIST_LPW_WINDOW_DAYS) / 7
    if weeks <= 0:
        return 0.0
    return leads / weeks


def tag_watchlist_unit(row: dict, today: date) -> tuple[str, str]:
    """Assign exactly ONE tag to a watchlist unit, first match wins.

    Order: WAIT, FIX, PRICE, FOLLOW-UP, CONVERT, LOW LEADS, REVIEW.

    Returns (tag, evidence_line).
    """
    dom = compute_dom(row.get("date_marked_available"), today)
    if dom is None:
        dom = 0
    leads = row.get("total_new_leads", 0)
    lpw = _compute_lpw(leads, dom)
    completed = row.get("showings_completed", 0)
    apps = row.get("applications_received", 0)
    price = row.get("unit_price") or row.get("advertised_rent") or ""
    market = row.get("market") or {}
    median = market.get("median_rent")
    pricing_position = market.get("pricing_position")

    base_evidence = (
        f"{dom} DOM, {lpw:.1f} leads/wk, "
        f"{completed} {'showing' if completed == 1 else 'showings'} done, "
        f"{apps} {'app' if apps == 1 else 'apps'}"
    )

    # WAIT: listed < 14 days, or excluded_from_dom, or future showing
    if dom < WATCHLIST_MIN_DOM_FOR_LEAD_RULE:
        return TAG_WAIT, base_evidence
    if row.get("excluded_from_dom"):
        return TAG_WAIT, base_evidence
    earliest_showing = row.get("unit_earliest_showing_date")
    if earliest_showing:
        try:
            es_date = datetime.fromisoformat(earliest_showing).astimezone(ET).date()
            if es_date > today:
                return TAG_WAIT, base_evidence
        except (ValueError, TypeError):
            pass

    # FIX: 2+ entries with keyword hits
    keyword_hits = _count_keyword_hits(row)
    if keyword_hits >= WATCHLIST_KEYWORD_HIT_THRESHOLD:
        return TAG_FIX, f"{base_evidence}, {keyword_hits} keyword {'hit' if keyword_hits == 1 else 'hits'}"

    # PRICE: overpriced AND 0 apps
    if pricing_position == "overpriced" and apps == 0:
        price_str = f"${int(price)}" if price else "?"
        median_str = f"${int(median)}" if median else "?"
        return TAG_PRICE, f"{base_evidence}, {price_str} vs {median_str} median"

    # FOLLOW-UP: many leads, few scheduled showings
    sched = row.get("showings_scheduled", 0)
    if leads >= FOLLOW_UP_LEADS_THRESHOLD and sched <= FOLLOW_UP_SHOWINGS_MAX:
        return TAG_FOLLOW_UP, base_evidence

    # CONVERT: many completed showings, few apps
    if completed >= CONVERT_SHOWINGS_MIN and apps <= CONVERT_APPS_MAX:
        return TAG_CONVERT, base_evidence

    # LOW LEADS: leads per week below threshold
    if lpw < LOW_LEADS_LPW_THRESHOLD:
        return TAG_LOW_LEADS, base_evidence

    # REVIEW: fallthrough
    return TAG_REVIEW, base_evidence


def compute_watchlist(pp_trailing_rows: list[dict], today: date) -> list[dict]:
    """Build the unit watchlist from 28-day trailing data.

    Flagged if:
      - DOM >= 30 (DOM trigger), OR
      - DOM >= 14 AND lpw < 4 (lead trigger), OR
      - 2+ keyword-hit entries (keyword trigger, always yellow)

    Units with DOM < 14 skip DOM and lead triggers but CAN be flagged
    by the keyword trigger.
    """
    rows = _available_rows(pp_trailing_rows)
    flagged = []

    for row in rows:
        dom = compute_dom(row.get("date_marked_available"), today)
        if dom is None:
            continue
        if row.get("excluded_from_dom"):
            continue

        leads = row.get("total_new_leads", 0)
        lpw = _compute_lpw(leads, dom)

        dom_trigger = dom >= WATCHLIST_DOM_TRIGGER
        lead_trigger = (
            dom >= WATCHLIST_MIN_DOM_FOR_LEAD_RULE and lpw < WATCHLIST_LPW_TRIGGER
        )
        keyword_hits = _count_keyword_hits(row)
        keyword_trigger = keyword_hits >= WATCHLIST_KEYWORD_HIT_THRESHOLD

        if not (dom_trigger or lead_trigger or keyword_trigger):
            continue

        # Color: red = both DOM + lead triggers, yellow = otherwise
        if dom_trigger and lead_trigger:
            color = "\U0001f534"  # red
        else:
            color = "\U0001f7e1"  # yellow

        tag, evidence = tag_watchlist_unit(row, today)

        flagged.append({
            "unit_id": row.get("unit_id"),
            "address": row.get("address", ""),
            "dom": dom,
            "tag": tag,
            "color": color,
            "evidence": evidence,
            "lpw": lpw,
            "keyword_hits": keyword_hits,
        })

    return flagged


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
