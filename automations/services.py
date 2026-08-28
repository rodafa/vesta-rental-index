"""
Automations services — shared Slack helper + onboarding handler.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def notify_slack(text: str, webhook_url: str | None = None) -> None:
    """POST a text message to a Slack webhook.

    When *webhook_url* is provided, posts there; otherwise falls back to
    settings.SLACK_WEBHOOK_URL.  Raises on misconfiguration or failed
    delivery — never silently drops.
    """
    url = webhook_url or settings.SLACK_WEBHOOK_URL
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


def update_leadsimple_stage(payload: dict) -> tuple[bool, str]:
    """Try to move the owner's LeadSimple deal to 'Onboard Form Filled Out'.

    Returns (ok, status_string). Never raises.
    """
    from integrations.leadsimple.deals import find_deal_by_email, move_deal_to_stage

    try:
        email = (payload.get("owner1_email") or "").strip()
        if not email:
            logger.info("onboard_leadsimple_no_email")
            return False, "LeadSimple: no owner email provided"

        pipeline_id = settings.LEADSIMPLE_OWNER_LEADS_PIPELINE_ID
        target_stage = settings.LEADSIMPLE_ONBOARD_FILLED_STAGE_ID

        result = find_deal_by_email(email, pipeline_id)

        if result["status"] == "no_match":
            logger.info(
                "onboard_leadsimple_no_match",
                extra={"email": email},
            )
            return False, "LeadSimple: no matching deal found"

        if result["status"] == "multiple":
            logger.warning(
                "onboard_leadsimple_multiple_matches",
                extra={"email": email, "count": result["count"]},
            )
            return False, f"LeadSimple: {result['count']} deals matched \u2014 none changed"

        deal = result["deal"]
        deal_id = deal["id"]
        current_stage = (deal.get("stage") or {}).get("id", "")

        if current_stage == target_stage:
            logger.info(
                "onboard_leadsimple_already_in_stage",
                extra={"deal_id": deal_id},
            )
            return True, "LeadSimple: already at Onboard Form Filled Out"

        move_deal_to_stage(deal_id, target_stage, pipeline_id)
        logger.info(
            "onboard_leadsimple_updated",
            extra={"deal_id": deal_id},
        )
        return True, "LeadSimple: moved to Onboard Form Filled Out"

    except Exception:
        logger.exception("onboard_leadsimple_error")
        return False, "LeadSimple: update failed (see logs)"


def handle_onboard_submission(payload: dict) -> None:
    """Format an onboarding form payload and send it to Slack."""
    owner = payload.get("owner1_name") or "\u2014"
    address = payload.get("property_address") or "\u2014"

    logger.info(
        "onboard_submission_received",
        extra={"owner1_name": owner, "property_address": address},
    )

    ls_ok, ls_status = update_leadsimple_stage(payload)
    ls_line = ls_status if ls_ok else f"\u26a0\ufe0f {ls_status}"

    message = (
        "*New onboarding form submitted*\n"
        f"Owner: {owner}\n"
        f"Property: {address}\n"
        f"City: {payload.get('property_city') or '\u2014'}\n"
        f"Plan: {payload.get('plan') or '\u2014'}\n"
        f"POC: {payload.get('poc_name') or '\u2014'}\n"
        f"Phone: {payload.get('poc_phone') or '\u2014'}\n"
        f"{ls_line}"
    )
    notify_slack(message)
    logger.info("onboard_slack_sent")
