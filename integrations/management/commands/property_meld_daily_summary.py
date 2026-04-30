"""
Management command: post Property Meld maintenance pulse to Slack.

Usage:
    python manage.py property_meld_daily_summary --dry-run
    python manage.py property_meld_daily_summary --channel maintenance-gpt
"""

import logging
from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from integrations.property_meld.mappers import OPEN_STATUSES
from maintenance.models import Meld

logger = logging.getLogger(__name__)

MAX_LIST = 5
ZAC_AGENT_ID = 52144  # Zac Fox — field supervisor
ZAC_HOURS_TARGET = 25

PRIORITY_KEYWORDS = [
    # Safety/health — multi-word first
    "gas leak", "carbon monoxide", "lead paint", "bed bug",
    "mold", "mildew", "asbestos", "sewage", "smoke", "fumes",
    "contamination", "hazardous", "biohazard", "rodent", "roach", "infestation", "pest",
    # Fire/water
    "burst pipe", "water intrusion", "ceiling leak", "roof leak", "water damage",
    "standing water", "raw sewage", "sewage backup", "sewer backup",
    "flooding", "flood", "fire",
    # Structural
    "ceiling falling", "ceiling caving", "wall crack", "floor collapsing",
    "railing broken", "stairs broken", "foundation", "collapse", "structural", "sinkhole", "balcony",
    # Critical systems — "no X" before bare word
    "no heat", "no hot water", "no water", "no electricity", "no power", "no air conditioning",
    "HVAC out", "furnace out", "heater not working", "AC not working", "breaker",
    # Legal/habitability
    "code violation", "health department", "housing authority", "called the city", "reported us",
    "uninhabitable", "condemned", "attorney", "lawyer", "lawsuit", "threatened",
    # Tenant distress
    "my child", "sick from", "emergency", "urgent", "dangerous", "unsafe",
    "baby", "elderly", "disabled", "medical", "hospital",
]


def _find_keyword_matches(text):
    t = (text or "").lower()
    return [kw for kw in PRIORITY_KEYWORDS if kw.lower() in t]


def _age_str(source_created_at, now):
    """Return human-readable age like '3h' or '5d'."""
    if not source_created_at:
        return "?"
    delta = now - source_created_at
    hours = int(delta.total_seconds() / 3600)
    if hours < 48:
        return f"{hours}h"
    return f"{delta.days}d"


def _days_since(dt, now):
    """Return whole days between dt and now."""
    if not dt:
        return "?"
    delta = now - dt
    return delta.days


def _green(name):
    return f"✅ *{name}* (0)"


def _expand(header, lines, total):
    parts = [header] + lines
    if total > MAX_LIST:
        parts.append(f"_...and {total - MAX_LIST} more_")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Section helpers
# ---------------------------------------------------------------------------

def _s1_emergencies(emergencies):
    name = "EMERGENCIES"
    total = len(emergencies)
    if total == 0:
        return _green(name)
    header = f"🚨 *{name}* ({total})"
    lines = []
    for m in emergencies[:MAX_LIST]:
        addr = (m.property_address or "No address")
        lines.append(f"• {addr}")
    return _expand(header, lines, total)


def _s2_net_flow(opened, closed):
    net = opened - closed
    sign = "+" if net >= 0 else ""
    return (
        f"📈 *NET FLOW — YESTERDAY*\n"
        f"Opened: {opened}  |  Closed: {closed}  |  Net: {sign}{net}"
    )


def _s3_unscheduled_aging(melds, now):
    name = "UNSCHEDULED & AGING (48h+)"
    total = len(melds)
    if total == 0:
        return _green(name)
    header = f"🔴 *{name}* ({total})"
    lines = []
    for m in melds[:MAX_LIST]:
        addr = (m.property_address or "No address")
        age = _age_str(m.source_created_at, now)
        lines.append(f"• {addr} — {age} old")
    return _expand(header, lines, total)


def _s4_repeat_addresses(rows):
    name = "REPEAT ADDRESSES (90 days)"
    total = len(rows)
    if total == 0:
        return _green(name)
    header = f"🔁 *{name}* ({total})"
    lines = []
    for row in rows[:MAX_LIST]:
        addr = row["property_address"] or "No address"
        count = row["meld_count"]
        lines.append(f"• {addr} — {count} melds")
    return _expand(header, lines, total)


def _s5_keyword_flags(pairs, today):
    name = "KEYWORD FLAGS"
    total = len(pairs)
    if total == 0:
        return _green(name)
    header = f"⚠️ *{name}* ({total})"
    lines = []
    for m, matches in pairs[:MAX_LIST]:
        addr = (m.property_address or "No address")
        kw_str = ", ".join(matches[:3])
        if m.source_created_at:
            age_days = (today - m.source_created_at.date()).days
            age = f"{age_days}d open"
        else:
            age = "?"
        lines.append(f"• [{kw_str}] {addr} — {age}")
    return _expand(header, lines, total)


def _s6_comms_gap(melds, now):
    name = "COMMS GAP (48h+ no activity)"
    total = len(melds)
    if total == 0:
        return _green(name)
    header = f"🟡 *{name}* ({total})"
    lines = []
    for m in melds[:MAX_LIST]:
        addr = (m.property_address or "No address")
        days = _days_since(m.source_modified_at, now)
        lines.append(f"• {addr} — {days}d since last activity")
    return _expand(header, lines, total)


def _s7_stale_approvals(melds, now):
    name = "STALE OWNER APPROVALS"
    total = len(melds)
    if total == 0:
        return _green(name)
    header = f"🟠 *{name}* ({total})"
    lines = []
    for m in melds[:MAX_LIST]:
        addr = (m.property_address or "No address")
        days = _days_since(m.source_modified_at, now)
        lines.append(f"• {addr} — {days}d waiting")
    return _expand(header, lines, total)


def _s8_vendor_ghosting(rows):
    name = "VENDOR GHOSTING (48h+ open)"
    total = len(rows)
    if total == 0:
        return _green(name)
    header = f"👻 *{name}* ({total})"
    lines = []
    for row in rows[:MAX_LIST]:
        vendor = row["assigned_vendor_name"] or "Unknown vendor"
        count = row["meld_count"]
        lines.append(f"• {vendor} — {count} melds")
    return _expand(header, lines, total)


def _s9_past_due(melds, today):
    name = "PAST-DUE SCHEDULED"
    total = len(melds)
    if total == 0:
        return _green(name)
    header = f"📅 *{name}* ({total})"
    lines = []
    for m in melds[:MAX_LIST]:
        addr = (m.property_address or "No address")
        if m.scheduled_date:
            overdue = (today - m.scheduled_date).days
            lines.append(f"• {addr} — {overdue}d overdue")
        else:
            lines.append(f"• {addr}")
    return _expand(header, lines, total)


def _s10_closed_yesterday(count):
    return f"✅ *CLOSED YESTERDAY*: {count}"


def _s11_zac_hours(hours):
    if hours is None:
        return f"🕐 *ZAC BILLABLE HOURS THIS WEEK*: N/A (fetch failed)"
    return f"🕐 *ZAC BILLABLE HOURS THIS WEEK*: {hours} / {ZAC_HOURS_TARGET}"


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = "Post Property Meld maintenance pulse to Slack."

    def add_arguments(self, parser):
        parser.add_argument("--channel", type=str, default="")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        channel = options["channel"] or getattr(settings, "PROPERTY_MELD", {}).get(
            "DAILY_SUMMARY_CHANNEL", ""
        )
        dry_run = options["dry_run"]

        now = timezone.now()
        today = date.today()
        yesterday = today - timedelta(days=1)
        cutoff_48h = now - timedelta(hours=48)
        cutoff_90d = today - timedelta(days=90)
        open_statuses = list(OPEN_STATUSES)

        # S1 — emergencies
        emergencies = list(
            Meld.objects.filter(status__in=open_statuses, priority="EMERGENCY")
            .order_by("source_created_at")
        )

        # S2 — net flow
        opened_yesterday = Meld.objects.filter(source_created_at__date=yesterday).count()
        closed_yesterday = Meld.objects.filter(completed_date=yesterday).count()

        # S3 — unscheduled & aging
        unscheduled_aging = list(
            Meld.objects.filter(
                status__in=open_statuses,
                assigned_vendor_name="",
                scheduled_date__isnull=True,
                source_created_at__isnull=False,
                source_created_at__lte=cutoff_48h,
            ).order_by("source_created_at")
        )

        # S4 — repeat addresses (last 90 days, all statuses — pattern detection)
        repeat_addresses = list(
            Meld.objects.filter(
                source_created_at__date__gte=cutoff_90d,
                property_address__gt="",
            )
            .values("property_address")
            .annotate(meld_count=Count("id"))
            .filter(meld_count__gte=2)
            .order_by("-meld_count")[:MAX_LIST]
        )

        # S5 — keyword flags (open melds only, Python-side matching)
        open_melds_for_kw = list(
            Meld.objects.filter(status__in=open_statuses)
            .only("brief_description", "property_address", "source_created_at")
        )
        keyword_flagged = []
        for meld in open_melds_for_kw:
            matches = _find_keyword_matches(meld.brief_description)
            if matches:
                keyword_flagged.append((meld, matches))

        # S6 — comms gap
        comms_gap = list(
            Meld.objects.filter(
                status__in=open_statuses,
                source_modified_at__isnull=False,
                source_modified_at__lt=cutoff_48h,
            ).order_by("source_modified_at")
        )

        # S7 — stale approvals (open only)
        stale_approvals = list(
            Meld.objects.filter(
                status__in=open_statuses,
                owner_approval_status="Requested",
                source_modified_at__isnull=False,
                source_modified_at__lt=cutoff_48h,
            ).order_by("source_modified_at")
        )

        # S8 — vendor ghosting (grouped by vendor)
        vendor_ghosting_rows = list(
            Meld.objects.filter(
                status__in=open_statuses,
                source_modified_at__isnull=False,
                source_modified_at__lt=cutoff_48h,
            )
            .exclude(assigned_vendor_name="")
            .values("assigned_vendor_name")
            .annotate(meld_count=Count("id"))
            .order_by("-meld_count")[:MAX_LIST]
        )

        # S9 — past due
        past_due = list(
            Meld.objects.filter(status__in=open_statuses, scheduled_date__lt=today)
            .order_by("scheduled_date")
        )

        # S11 — Zac hours
        zac_hours = self._fetch_zac_hours(today)

        if dry_run:
            self._print(
                emergencies, opened_yesterday, closed_yesterday,
                unscheduled_aging, repeat_addresses, keyword_flagged,
                comms_gap, stale_approvals, vendor_ghosting_rows,
                past_due, closed_yesterday, zac_hours, today,
            )
            return

        if not channel:
            self.stderr.write(self.style.ERROR(
                "No Slack channel configured. Set DAILY_SUMMARY_CHANNEL or pass --channel."
            ))
            return

        self._post(
            channel,
            emergencies, opened_yesterday, closed_yesterday,
            unscheduled_aging, repeat_addresses, keyword_flagged,
            comms_gap, stale_approvals, vendor_ghosting_rows,
            past_due, closed_yesterday, zac_hours, today,
        )

    def _fetch_zac_hours(self, today):
        """Fetch Zac Fox's billable hours for the current calendar week."""
        try:
            from integrations.property_meld.client import PropertyMeldClient
            client = PropertyMeldClient()
            monday = today - timedelta(days=today.weekday())

            data = client.get("/work_log/", params={
                "limit": 500,
                "agent": ZAC_AGENT_ID,
            })
            records = data.get("results", []) if isinstance(data, dict) else data

            total = sum(
                float(r.get("hours") or 0)
                for r in records
                if r.get("agent") == ZAC_AGENT_ID
                and r.get("checkin")
                and monday.isoformat() <= r["checkin"][:10] <= today.isoformat()
            )
            return round(total, 1)
        except Exception as exc:
            logger.warning("Could not fetch Zac's work log hours: %s", exc)
            return None

    def _build_sections(self, emergencies, opened_yesterday, closed_yesterday,
                        unscheduled_aging, repeat_addresses, keyword_flagged,
                        comms_gap, stale_approvals, vendor_ghosting_rows,
                        past_due, closed_yesterday_count, zac_hours, today):
        day_str = today.strftime("%A, %B %-d")
        now = timezone.now()
        return [
            f"🏠 *Maintenance Pulse — {day_str}*",
            _s1_emergencies(emergencies),
            _s2_net_flow(opened_yesterday, closed_yesterday),
            _s3_unscheduled_aging(unscheduled_aging, now),
            _s4_repeat_addresses(repeat_addresses),
            _s5_keyword_flags(keyword_flagged, today),
            _s6_comms_gap(comms_gap, now),
            _s7_stale_approvals(stale_approvals, now),
            _s8_vendor_ghosting(vendor_ghosting_rows),
            _s9_past_due(past_due, today),
            _s10_closed_yesterday(closed_yesterday_count),
            _s11_zac_hours(zac_hours),
        ]

    def _to_blocks(self, sections):
        blocks = []
        for text in sections:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})
            blocks.append({"type": "divider"})
        if blocks and blocks[-1]["type"] == "divider":
            blocks.pop()
        return blocks

    def _print(self, *args):
        sections = self._build_sections(*args)
        self.stdout.write("\n=== Maintenance Pulse DRY RUN ===\n")
        for s in sections:
            self.stdout.write(s)
            self.stdout.write("---")
        self.stdout.write("")

    def _post(self, channel, *args):
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError

        token = getattr(settings, "SLACK_BOT_TOKEN", "")
        if not token:
            self.stderr.write(self.style.ERROR("SLACK_BOT_TOKEN not configured."))
            return

        sections = self._build_sections(*args)
        blocks = self._to_blocks(sections)
        today = args[-1]  # last positional arg

        if not channel.startswith(("#", "C", "G", "D", "W")):
            channel = f"#{channel}"

        client = WebClient(token=token)
        try:
            response = client.chat_postMessage(
                channel=channel,
                text=f"🏠 Maintenance Pulse — {today.strftime('%A, %B %-d')}",
                blocks=blocks,
            )
            self.stdout.write(self.style.SUCCESS(
                f"Posted to {channel} (ts={response['ts']})"
            ))
        except SlackApiError as exc:
            self.stderr.write(self.style.ERROR(f"Slack API error: {exc.response['error']}"))
