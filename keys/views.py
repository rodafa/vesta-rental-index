"""Slack Events API endpoint for KeyBot."""

import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from keys.slack import post_message, verify_slack_signature

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def slack_events(request):
    """Receive Slack Events API callbacks for KeyBot."""
    # --- Signature verification ---
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    if not verify_slack_signature(
        settings.SLACK_SIGNING_SECRET, timestamp, request.body, signature
    ):
        logger.warning("keybot_invalid_signature")
        return JsonResponse({"error": "invalid signature"}, status=401)

    # --- Retry suppression ---
    if request.headers.get("X-Slack-Retry-Num"):
        logger.info("keybot_retry_skipped")
        return JsonResponse({"ok": True})

    body = json.loads(request.body)
    event_type = body.get("type")

    # --- URL verification handshake ---
    if event_type == "url_verification":
        logger.info("keybot_url_verification")
        return JsonResponse({"challenge": body.get("challenge", "")})

    # --- Event callbacks ---
    if event_type == "event_callback":
        event = body.get("event", {})
        if event.get("type") == "app_mention":
            channel = event.get("channel")
            if channel != settings.KEYBOT_CHANNEL_ID:
                logger.info("keybot_ignored_channel", extra={"channel": channel})
                return JsonResponse({"ok": True})
            if event.get("bot_id"):
                logger.info("keybot_ignored_bot")
                return JsonResponse({"ok": True})
            text = event.get("text", "")
            logger.info(
                "keybot_app_mention",
                extra={"channel": channel, "text": text[:100]},
            )
            post_message(
                channel,
                "KeyBot is connected \u2705 \u2014 lookups coming soon.",
                thread_ts=event.get("ts"),
            )
            return JsonResponse({"ok": True})

    logger.info("keybot_unhandled_event", extra={"type": event_type})
    return JsonResponse({"ok": True})
