"""Post weekly update run summary to Slack."""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_DASHBOARD_BASE = "https://dashboard.vestapm.com"


def post_run_summary(run):
    """Post a summary of a WeeklyReportRun to the leasing Slack channel.

    Args:
        run: WeeklyReportRun model instance.
    """
    webhook_url = getattr(settings, "SLACK_LEASING_WEBHOOK_URL", "")
    if not webhook_url:
        logger.debug("SLACK_LEASING_WEBHOOK_URL not configured, skipping Slack post")
        return

    end_label = run.end_date.strftime("%a %b %-d, %Y")

    lines = [
        f":bar_chart: *Weekly Report Run* — Week ending {end_label}",
        f"   :white_check_mark: Notes drafted: {run.notes_drafted}",
        f"   :fast_forward: Skipped (no data / approved): {run.units_skipped}",
        f"   :warning: Errors: {run.units_errored}",
        f"   Review queue: {_DASHBOARD_BASE}/dashboard/weekly-reports/?status=draft",
        f"   Run ID: {run.id}",
    ]

    text = "\n".join(lines)

    try:
        resp = requests.post(
            webhook_url,
            json={"text": text},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Posted weekly update summary to Slack")
    except Exception:
        logger.exception("Failed to post weekly update summary to Slack")
