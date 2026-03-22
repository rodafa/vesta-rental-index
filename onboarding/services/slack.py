import logging

from django.conf import settings
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)


def post_slack_summary(onboard):
    """Post a summary card to Slack when a new owner onboarding form is submitted."""
    token = getattr(settings, "SLACK_BOT_TOKEN", "")
    if not token:
        logger.warning("SLACK_BOT_TOKEN not configured — skipping Slack notification")
        return

    channel = getattr(settings, "SLACK_ONBOARDING_CHANNEL", "#onboarding")
    submitted = onboard.submitted_at.strftime("%Y-%m-%d %H:%M UTC")
    text = (
        "\U0001f3e0 *New Owner Onboarding \u2014 {address}*\n"
        "Owner: {owner}\n"
        "Plan: {plan}\n"
        "Occupied: {occupied}\n"
        "HOA: {hoa}\n"
        "Pets: {pets}\n"
        "Submitted: {submitted}"
    ).format(
        address=onboard.property_address,
        owner=onboard.owner1_name or "\u2014",
        plan=onboard.plan or "\u2014",
        occupied=onboard.occupied or "\u2014",
        hoa=onboard.hoa or "\u2014",
        pets=onboard.pet_policy or "\u2014",
        submitted=submitted,
    )

    try:
        client = WebClient(token=token)
        client.chat_postMessage(channel=channel, text=text)
    except SlackApiError:
        logger.exception("Slack API error posting onboarding summary for #%d", onboard.id)
