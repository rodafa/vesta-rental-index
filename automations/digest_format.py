"""
Leasing digest formatting — pure functions that turn metrics into Slack mrkdwn.

No API calls, no metric computation. Input: computed metrics. Output: text strings.
"""

import re

from automations.digest_config import (
    DOM_LIST_MAX_DISPLAY,
    MESSAGE_MAX_CHARS,
    REPLY_TIME_GOAL_DISPLAY,
    REPLY_TIME_LABEL,
    TEAM_PASS_THRESHOLD,
    WEEKLY_GOALS,
)


def format_weekly_digest(
    scorecard: dict,
    reply_time: float | None,
    reply_time_history: list[dict],
    dom_units: list[dict],
    week_label: str,
) -> list[str]:
    """Format the full weekly digest as a list of Slack messages.

    Each message stays under MESSAGE_MAX_CHARS. Splits at section boundaries.
    """
    sections = []

    # --- Section 1: Scorecard header ---
    green = scorecard["green_count"]
    total = scorecard["total_graded"]
    team_emoji = "\U0001f7e2" if scorecard["team_pass"] else "\U0001f534"
    header = (
        f"*Leasing Scorecard \u2014 {week_label}*\n"
        f"Team: {green} of {total} graded green "
        f"(need {TEAM_PASS_THRESHOLD}) {team_emoji}"
    )

    # --- Section 2: Measurable lines ---
    lines = [header, ""]
    for key, label, value, goal_display, is_green in scorecard["results"]:
        emoji = "\U0001f7e2" if is_green else "\U0001f534"
        display = _format_value(key, value)
        lines.append(f"{emoji} *{label}*  {display}  (goal: {goal_display})")

    # Reply time line (ungraded)
    if reply_time is not None:
        lines.append(
            f"\u26aa *{REPLY_TIME_LABEL}*  {reply_time:.1f}m  "
            f"(tracking \u2014 goal: {REPLY_TIME_GOAL_DISPLAY})"
        )

    sections.append("\n".join(lines))

    # --- Section 3: Reply time trend ---
    if reply_time is not None or reply_time_history:
        trend_lines = [f"\n*{REPLY_TIME_LABEL} \u2014 last 4 weeks*"]
        if reply_time is not None:
            trend_lines.append(f"\u2022 This week: {reply_time:.1f} min")
        for entry in reply_time_history:
            val = entry.get("value")
            label = entry.get("label", "")
            if val is not None:
                trend_lines.append(f"\u2022 {label}: {val:.1f} min")
        sections.append("\n".join(trend_lines))

    # --- Section 4: Units past 30 DOM ---
    dom_lines = ["\n*Units past 30 DOM*"]
    if dom_units:
        for u in dom_units[:DOM_LIST_MAX_DISPLAY]:
            dom_lines.append(f"\u2022 {u['address']} \u2014 {u['dom']} DOM")
        overflow = len(dom_units) - DOM_LIST_MAX_DISPLAY
        if overflow > 0:
            dom_lines.append(f"+{overflow} more")
    else:
        dom_lines.append("None")
    sections.append("\n".join(dom_lines))

    return _split_messages(sections)


def format_daily_digest(
    tasks_by_type: dict,
    showings_today: list[dict],
    open_apps: list[dict],
    stale_count: int,
    yesterday_leads: int,
    calls_answered: int = 0,
    calls_total: int = 0,
    call_rate: int | None = None,
    reply_time: float | None = None,
    dom_count: int = 0,
    app_stages: dict | None = None,
    address_lookup: dict | None = None,
    today_label: str = "",
) -> list[str]:
    """Format the daily digest as a list of Slack messages."""
    from automations.digest_data import resolve_address

    app_stages = app_stages or {}
    address_lookup = address_lookup or {}
    sections = []

    # Header
    sections.append(f"*Leasing Daily \u2014 {today_label}*")

    # --- Needs Action ---
    if tasks_by_type:
        task_lines = ["\n*Needs Action*"]
        for task_type, items in tasks_by_type.items():
            count = len(items)
            # Find oldest
            oldest_hours = 0
            for item in items:
                created = item.get("created_at")
                if created:
                    try:
                        from datetime import datetime
                        from zoneinfo import ZoneInfo
                        dt = datetime.fromisoformat(created)
                        hours = (datetime.now(ZoneInfo("UTC")) - dt).total_seconds() / 3600
                        oldest_hours = max(oldest_hours, hours)
                    except (ValueError, TypeError):
                        pass
            age_str = _format_age(oldest_hours)
            task_lines.append(f"\u2022 {task_type}: {count} (oldest {age_str})")
        sections.append("\n".join(task_lines))

    # --- Yesterday ---
    yest_lines = ["\n*Yesterday*"]
    yest_lines.append(f"\u2022 New leads: {yesterday_leads}")
    if calls_total > 0:
        rate_str = f" ({call_rate}%)" if call_rate is not None else ""
        yest_lines.append(f"\u2022 Calls: {calls_answered} answered / {calls_total} total{rate_str}")
    if reply_time is not None:
        yest_lines.append(f"\u2022 Reply time: {reply_time:.1f} min")
    sections.append("\n".join(yest_lines))

    # --- Today's Showings ---
    if showings_today:
        showing_lines = ["\n*Today's Showings*"]
        for s in showings_today:
            agent_part = f" \u2014 {s['agent']}" if s["agent"] else ""
            showing_lines.append(
                f"\u2022 {s['time_et']} \u2014 {s['address']}"
                f"{agent_part} \u2014 {s['status_emoji']} {s['status_label']}"
            )
        sections.append("\n".join(showing_lines))

    # --- Applications ---
    if open_apps:
        stale_label = f", {stale_count} over 72h \U0001f534" if stale_count else ""
        app_lines = [f"\n*Applications* ({len(open_apps)} open{stale_label})"]
        for app in open_apps:
            uid = app.get("unit_id")
            address = resolve_address(uid, address_lookup) if uid else "Unknown"
            hours = app.get("_hours_open", 0)
            age = _format_age(hours)
            status = app.get("status", "")
            gid = str(app.get("id", ""))
            stage = app_stages.get(gid)
            stage_part = f" \u2014 {stage}" if stage else ""
            app_lines.append(f"\u2022 {address} \u2014 {age} \u2014 {status}{stage_part}")
        sections.append("\n".join(app_lines))

    # --- DOM count ---
    if dom_count > 0:
        sections.append(f"\n\U0001f534 *{dom_count} units past 30 DOM*")

    return _split_messages(sections)


def _format_value(key: str, value) -> str:
    """Format a metric value for display."""
    if value is None:
        return "N/A"
    if key in ("showing_completion", "lead_to_showing", "call_answer_rate", "feedback_captured"):
        return f"{value}%"
    return str(value)


def _format_age(hours: float) -> str:
    """Format an age in hours as a human-readable string."""
    if hours < 1:
        return "<1h"
    if hours < 24:
        return f"{int(hours)}h"
    days = int(hours / 24)
    return f"{days}d"


def _split_messages(sections: list[str], max_chars: int = MESSAGE_MAX_CHARS) -> list[str]:
    """Combine sections into messages, splitting at section boundaries."""
    messages = []
    current = ""

    for section in sections:
        candidate = current + ("\n" if current else "") + section
        if len(candidate) > max_chars and current:
            messages.append(current.strip())
            current = section
        else:
            current = candidate

    if current.strip():
        messages.append(current.strip())

    return messages


def check_dom_list_render(messages: list[str], expected_total: int) -> None:
    """Verify rendered DOM list matches expected unit count.

    Parses the DOM list block from the formatted messages. Checks:
      1. Bullet lines under "*Units past 30 DOM*"
      2. Plus any "+N more" overflow
      3. Total == expected_total

    A "None" line (zero units) counts as 0.
    Raises ValueError on any mismatch.
    """
    full_text = "\n".join(messages)

    if "*Units past 30 DOM*" not in full_text:
        raise ValueError(
            f"No DOM list block found but expected {expected_total} units"
        )

    # Extract the DOM block: from "*Units past 30 DOM*" to end of that section
    dom_start = full_text.index("*Units past 30 DOM*")
    dom_block = full_text[dom_start:]

    # Count bullet lines (address — N DOM)
    bullet_lines = re.findall(r"^\u2022 .+ \u2014 \d+ DOM$", dom_block, re.MULTILINE)

    # Check for "None" line (zero units)
    has_none = bool(re.search(r"^None$", dom_block, re.MULTILINE))

    # Check overflow line
    overflow_match = re.search(r"^\+(\d+) more$", dom_block, re.MULTILINE)
    overflow_n = int(overflow_match.group(1)) if overflow_match else 0

    rendered_total = len(bullet_lines) + overflow_n

    if has_none and expected_total != 0:
        raise ValueError(
            f"DOM list shows 'None' but expected {expected_total} units"
        )
    if not has_none and rendered_total != expected_total:
        raise ValueError(
            f"Rendered DOM list total {rendered_total} "
            f"({len(bullet_lines)} lines + {overflow_n} overflow) "
            f"!= expected {expected_total}"
        )
