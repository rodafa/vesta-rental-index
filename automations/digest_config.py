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
    ("units_leased", "Units leased", 2, "gte", "2+"),
    ("apps_submitted", "Apps submitted", 7, "gte", "7+"),
    ("showing_completion", "Showing completion", 70, "gte", "70%+"),
    ("lead_to_showing", "Lead\u2192showing", 35, "gte", "35%+"),
    ("call_answer_rate", "Call answer rate", 85, "gte", "85%+"),
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
# Watchlist flag triggers
# ---------------------------------------------------------------------------
WATCHLIST_DOM_TRIGGER = 30          # DOM >= this fires DOM trigger
WATCHLIST_LPW_TRIGGER = 4.0        # leads/week < this fires lead trigger
WATCHLIST_MIN_DOM_FOR_LEAD_RULE = 14  # skip lead rule if DOM < this
WATCHLIST_KEYWORD_HIT_THRESHOLD = 2   # entries with hits >= this fires keyword trigger
WATCHLIST_LPW_WINDOW_DAYS = 28      # trailing window for lpw denominator

# ---------------------------------------------------------------------------
# Watchlist tag thresholds (first match wins in tag_watchlist_unit)
# Order: WAIT, FIX, PRICE, FOLLOW-UP, CONVERT, LOW LEADS, REVIEW
# ---------------------------------------------------------------------------
TAG_WAIT = "WAIT"
TAG_FIX = "FIX"
TAG_PRICE = "PRICE"
TAG_FOLLOW_UP = "FOLLOW-UP"
TAG_CONVERT = "CONVERT"
TAG_LOW_LEADS = "LOW LEADS"
TAG_REVIEW = "REVIEW"

# Tag display order (for watchlist sort and overflow counts)
TAG_DISPLAY_ORDER = [
    TAG_FIX, TAG_PRICE, TAG_CONVERT, TAG_FOLLOW_UP,
    TAG_LOW_LEADS, TAG_REVIEW,
]

# WAIT is excluded from display order — WAIT units are flagged but
# shown after all other tags (they are not actionable).

# FIX: 2+ showing_complete_context entries with keyword matches
FIX_KEYWORD_HIT_THRESHOLD = 2

# PRICE: overpriced AND applications_received == 0
# (no additional thresholds beyond pricing_position)

# FOLLOW-UP: total_new_leads >= threshold AND showings_scheduled <= threshold
FOLLOW_UP_LEADS_THRESHOLD = 10
FOLLOW_UP_SHOWINGS_MAX = 2

# CONVERT: showings_completed >= threshold AND applications_received <= threshold
CONVERT_SHOWINGS_MIN = 4
CONVERT_APPS_MAX = 1

# LOW LEADS: leads per week < threshold
LOW_LEADS_LPW_THRESHOLD = 2.0

# ---------------------------------------------------------------------------
# Keyword regex for FIX tag (condition keywords in showing feedback)
# ---------------------------------------------------------------------------
# Leading \b, no trailing \b — matches word-start prefixes.
# "whole" -> no match, "painted" -> match, "cleaning" -> match.
FIX_KEYWORD_RE = re.compile(
    r"\b(odor|smell|musty|paint|hole|crack|dirty|clean|damage|dilapidated|mold)",
    re.IGNORECASE,
)

# Fields in showing_complete_context to scan for keywords.
FIX_KEYWORD_FIELDS = ("maintenanceIssues", "prospectDisliked")

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
WATCHLIST_MAX_DISPLAY = 10
MESSAGE_MAX_CHARS = 3500

# ---------------------------------------------------------------------------
# Placeholder address pattern (excluded from all measurables and watchlist)
# ---------------------------------------------------------------------------
PLACEHOLDER_RE = re.compile(r"applications only", re.IGNORECASE)
