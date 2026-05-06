"""
Post daily leasing summary to Slack via incoming webhook.

Usage:
    python manage.py post_leasing_summary [--date YYYY-MM-DD]

Mirrors the Apps Script format: grand totals + per-property breakdown
sorted by activity descending.
"""

import json
import logging
from collections import defaultdict
from datetime import timedelta

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from leasing.models import DailyLeasingMetric

logger = logging.getLogger(__name__)


def _build_summary(target_date):
    """Build summary data from DailyLeasingMetric rows for the given date."""
    metrics = DailyLeasingMetric.objects.filter(
        date=target_date
    ).select_related("unit__property")

    if not metrics.exists():
        return None

    # Grand totals
    totals = metrics.aggregate(
        prospects=Sum("new_prospects"),
        showings=Sum("showings_completed"),
        applications=Sum("applications_submitted"),
        missed=Sum("showings_missed_or_failed"),
    )

    # Per-property breakdown
    by_property = defaultdict(lambda: {
        "prospects": 0, "showings": 0, "applications": 0, "missed": 0,
    })

    for m in metrics:
        addr = m.unit.display_address
        by_property[addr]["prospects"] += m.new_prospects
        by_property[addr]["showings"] += m.showings_completed
        by_property[addr]["applications"] += m.applications_submitted
        by_property[addr]["missed"] += m.showings_missed_or_failed

    # Sort by total activity descending
    sorted_props = sorted(
        by_property.items(),
        key=lambda x: (
            x[1]["prospects"] + x[1]["showings"]
            + x[1]["missed"] + x[1]["applications"]
        ),
        reverse=True,
    )

    return {
        "date": target_date,
        "totals": totals,
        "properties": sorted_props,
        "unit_count": metrics.count(),
    }


def _format_slack_message(summary):
    """Format summary data into a Slack message payload (Apps Script style)."""
    t = summary["totals"]
    d = summary["date"]
    date_str = f"{d.strftime('%A')}, {d.strftime('%B')} {d.day}, {d.year}"

    lines = [
        f"\u2705 Daily Leasing Summary \u2014 {date_str}",
        f"Grand Totals ({summary['unit_count']} units):",
        f"\U0001f525 New Leads: {t['prospects'] or 0}",
        f"\U0001f440 Showings Completed: {t['showings'] or 0}",
        f"\U0001f6ab Showings Missed/Failed: {t['missed'] or 0}",
        f"\U0001f4dd Applications Received: {t['applications'] or 0}",
        "Property Breakdown:",
    ]

    for addr, counts in summary["properties"]:
        activity = (
            counts["prospects"] + counts["showings"]
            + counts["missed"] + counts["applications"]
        )
        if activity == 0:
            continue
        lines.append(
            f"\u2022 *{addr}*: "
            f"{counts['prospects']} Leads | "
            f"{counts['showings']} Showings | "
            f"{counts['missed']} Missed | "
            f"{counts['applications']} Apps"
        )

    return {"text": "\n".join(lines)}


class Command(BaseCommand):
    help = "Post daily leasing summary to Slack."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Date for the summary (YYYY-MM-DD). Defaults to yesterday.",
        )

    def handle(self, *args, **options):
        from datetime import date as date_type

        if options["date"]:
            target_date = date_type.fromisoformat(options["date"])
        else:
            target_date = (timezone.now() - timedelta(days=1)).date()

        webhook_url = settings.SLACK_LEASING_WEBHOOK_URL
        if not webhook_url:
            self.stderr.write(
                "SLACK_LEASING_WEBHOOK_URL not configured. Skipping.\n"
            )
            return

        summary = _build_summary(target_date)
        if summary is None:
            self.stdout.write(
                f"No leasing metrics found for {target_date}. "
                "Nothing to post.\n"
            )
            return

        payload = _format_slack_message(summary)
        self.stdout.write(f"Posting summary for {target_date}...\n")

        resp = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=15,
        )

        if resp.status_code == 200:
            self.stdout.write("Posted successfully.\n")
        else:
            self.stderr.write(
                f"Slack webhook returned {resp.status_code}: {resp.text}\n"
            )
