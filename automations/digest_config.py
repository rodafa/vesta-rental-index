"""
Leasing digest configuration — goals, thresholds, and keyword lists.

All magic numbers live here so tests and business logic share one source.
"""

import re

# ---------------------------------------------------------------------------
# Weekly scorecard goals (8 graded measurables)
# ---------------------------------------------------------------------------
# Each tuple: (key, label, goal_value, direction, goal_display)
#   direction: "gte" = green when >= goal, "lte" = green when <= goal
WEEKLY_GOALS = [
    ("units_leased", "Units leased", 3, "gte", "3+"),
    ("apps_submitted", "Apps submitted", 4, "gte", "4+"),
    ("showing_completion", "Showing completion", 70, "gte", "70%+"),
    ("lead_to_showing", "Lead\u2192showing", 35, "gte", "35%+"),
    ("call_answer_rate", "Call answer rate", 70, "gte", "70%+"),
    # measurable 6 (reply time) is UNGRADED — not in this list
    ("feedback_captured", "Feedback captured", 90, "gte", "90%+"),
    ("stale_apps", "Stale apps (>72h)", 0, "lte", "0"),
    ("units_past_dom", "Units 30+ DOM", 10, "lte", "\u226410"),
]

# Team passes when this many graded measurables are green.
TEAM_PASS_THRESHOLD = 6

# Reply time is shown but not graded.
REPLY_TIME_LABEL = "Avg text reply time (excl. AI)"
REPLY_TIME_GOAL_DISPLAY = "\u226415m"
REPLY_TIME_GOAL_MINUTES = 15

# ---------------------------------------------------------------------------
# DOM threshold
# ---------------------------------------------------------------------------
DOM_THRESHOLD = 30

# ---------------------------------------------------------------------------
# Showing status mapping (RentEngine status -> display category)
# ---------------------------------------------------------------------------
SHOWING_STATUS_MAP = {
    "Showing Complete": ("Complete", "\U0001f7e2"),
    "Showing Confirmed": ("Confirmed", "\U0001f535"),
    "Showing Canceled": ("Canceled", "\U0001f534"),
    "Showing Failed": ("Failed", "\U0001f534"),
    "Missed Showing": ("Missed", "\U0001f534"),
}
SHOWING_STATUS_DEFAULT = ("Unknown", "\U0001f534")

# Statuses counted as "completed" for showing completion rate.
SHOWING_COMPLETE_STATUSES = {"Showing Complete"}

# ---------------------------------------------------------------------------
# Application status classification
# ---------------------------------------------------------------------------
APP_OPEN_STATUSES = {"Submitted", "Awaiting Additional Info"}
APP_STALE_HOURS = 72

# ---------------------------------------------------------------------------
# Display limits
# ---------------------------------------------------------------------------
MESSAGE_MAX_CHARS = 3500
DOM_LIST_MAX_DISPLAY = 20

# ---------------------------------------------------------------------------
# Placeholder address pattern (excluded from all measurables)
# ---------------------------------------------------------------------------
PLACEHOLDER_RE = re.compile(r"applications only", re.IGNORECASE)
