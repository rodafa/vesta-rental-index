"""
Automations services — shared Slack helper + onboarding handler.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def notify_slack(text: str) -> None:
    """POST a text message to the configured Slack webhook.

    Raises on misconfiguration or failed delivery — never silently drops.
    """
    url = settings.SLACK_WEBHOOK_URL
    if not url:
        logger.error("slack_webhook_not_configured")
        raise RuntimeError("SLACK_WEBHOOK_URL is not configured")

    resp = requests.post(url, json={"text": text}, timeout=10)
    if resp.status_code != 200:
        logger.error(
            "slack_send_failed",
            extra={"status": resp.status_code, "body": resp.text[:500]},
        )
        raise RuntimeError(
            f"Slack webhook returned {resp.status_code}: {resp.text[:200]}"
        )


def handle_onboard_submission(payload: dict) -> None:
    """Format an onboarding form payload and send it to Slack."""
    owner = payload.get("owner1_name") or "\u2014"
    address = payload.get("property_address") or "\u2014"

    logger.info(
        "onboard_submission_received",
        extra={"owner1_name": owner, "property_address": address},
    )

    message = (
        "*New onboarding form submitted*\n"
        f"Owner: {owner}\n"
        f"Property: {address}\n"
        f"City: {payload.get('property_city') or '\u2014'}\n"
        f"Plan: {payload.get('plan') or '\u2014'}\n"
        f"POC: {payload.get('poc_name') or '\u2014'}\n"
        f"Phone: {payload.get('poc_phone') or '\u2014'}"
    )
    notify_slack(message)
