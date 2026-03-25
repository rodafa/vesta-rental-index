"""
Management command: post Property Meld maintenance pulse to Slack.

Usage:
    python manage.py property_meld_daily_summary --dry-run
    python manage.py property_meld_daily_summary --channel maintenance-minute
"""

import logging
from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from integrations.property_meld.mappers import OPEN_STATUSES, VENDOR_ACCEPTED_STATUSES
from maintenance.models import Meld

logger = logging.getLogger(__name__)

MAX_PER_SECTION = 10
ZAC_AGENT_ID = 52144  # Zac Fox — field supervisor


def _days_overdue(scheduled_date):
    return (date.today() - scheduled_date).days


def _meld_line(meld, show_overdue=False):
    desc = (meld.brief_description or f"Meld {meld.property_meld_id}")[:55]
    addr = (meld.property_address or "No address")[:45]
    line = f"• {desc} — {addr}"
    if show_overdue and meld.scheduled_date:
        line += f" *({_days_overdue(meld.scheduled_date)}d overdue)*"
    return line


def _section(emoji, title, action, melds, show_overdue=False):
    count = len(melds)
    header = f"{emoji} *{title}* ({count})"
    if action:
        header += f" → {action}"
    if count == 0:
        return header
    lines = [header]
    for m in melds[:MAX_PER_SECTION]:
        lines.append(_meld_line(m, show_overdue=show_overdue))
    if count > MAX_PER_SECTION:
        lines.append(f"_...and {count - MAX_PER_SECTION} more_")
    return "\n".join(lines)


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
        cutoff_48h = now - timedelta(hours=48)
        cutoff_24h = now - timedelta(hours=24)
        open_statuses = list(OPEN_STATUSES)

        # --- Section queries ---
        emergencies = list(
            Meld.objects.filter(status__in=open_statuses, priority="EMERGENCY")
            .order_by("source_created_at")
        )
        no_coordinator = list(
            Meld.objects.filter(status__in=open_statuses, coordinator_name="")
            .order_by("source_created_at")
        )
        no_vendor = list(
            Meld.objects.filter(status__in=open_statuses, assigned_vendor_name="")
            .exclude(priority="EMERGENCY")
            .order_by("source_created_at")
        )
        vendor_ghosting = list(
            Meld.objects.filter(
                status__in=list(VENDOR_ACCEPTED_STATUSES),
                scheduled_date__isnull=True,
                source_modified_at__isnull=False,
                source_modified_at__lte=cutoff_48h,
            ).order_by("source_modified_at")
        )
        past_due = list(
            Meld.objects.filter(status__in=open_statuses, scheduled_date__lt=today)
            .order_by("scheduled_date")
        )
        stale_approvals = list(
            Meld.objects.filter(
                owner_approval_status="Requested",
                source_modified_at__isnull=False,
                source_modified_at__lte=cutoff_24h,
            ).order_by("source_modified_at")
        )

        # --- Scorecard ---
        total_open = Meld.objects.filter(status__in=open_statuses).count()
        total_unscheduled = Meld.objects.filter(
            status__in=open_statuses, scheduled_date__isnull=True
        ).count()
        annual_inspections_mtd = Meld.objects.filter(
            brief_description__icontains="annual inspection",
            status="COMPLETED",
            completed_date__gte=today.replace(day=1),
        ).count()
        zac_hours = self._fetch_zac_hours(today)

        if dry_run:
            self._print(
                emergencies, no_coordinator, no_vendor, vendor_ghosting,
                past_due, stale_approvals, zac_hours,
                annual_inspections_mtd, total_open, total_unscheduled, today,
            )
            return

        if not channel:
            self.stderr.write(self.style.ERROR(
                "No Slack channel configured. Set PROPERTY_MELD_SUMMARY_CHANNEL or pass --channel."
            ))
            return

        self._post(
            channel, emergencies, no_coordinator, no_vendor, vendor_ghosting,
            past_due, stale_approvals, zac_hours,
            annual_inspections_mtd, total_open, total_unscheduled, today,
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

    def _build_sections(self, emergencies, no_coordinator, no_vendor, vendor_ghosting,
                        past_due, stale_approvals, zac_hours,
                        annual_inspections_mtd, total_open, total_unscheduled, today):
        day_str = today.strftime("%A, %B %-d")
        hours_str = f"{zac_hours} / 20" if zac_hours is not None else "N/A"
        scorecard = (
            f"📊 *SCORECARD*\n"
            f"Zac billable hours this week: {hours_str}\n"
            f"Annual inspections MTD: {annual_inspections_mtd} completed\n"
            f"Total open melds: {total_open} | Unscheduled: {total_unscheduled}"
        )
        return [
            f"🏠 *Maintenance Pulse — {day_str}*",
            _section("🚨", "EMERGENCIES", None, emergencies),
            _section("🔴", "NO COORDINATOR ASSIGNED", "Camilo", no_coordinator),
            _section("🔴", "NO VENDOR ASSIGNED", "Assign vendor", no_vendor),
            _section("🟡", "VENDOR GHOSTING", "Camilo: follow up", vendor_ghosting),
            _section("🟡", "PAST-DUE SCHEDULED", "Camilo: follow up", past_due, show_overdue=True),
            _section("🟡", "STALE OWNER APPROVALS", "Follow up with owner", stale_approvals),
            scorecard,
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
