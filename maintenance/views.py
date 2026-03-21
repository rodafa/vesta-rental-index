import json
import logging
import re
import threading

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .ai_service import handle_mention
from .slack_bot import verify_slack_signature

logger = logging.getLogger(__name__)


def _process_mention(user_text, channel, thread_ts):
    """Runs in a background thread — calls Claude then posts reply to Slack."""
    try:
        response_text = handle_mention(user_text, thread_ts, channel)
    except Exception:
        logger.exception("Error calling Claude for app_mention")
        response_text = "Sorry, I ran into an error. Please try again."

    try:
        client = WebClient(token=settings.SLACK_BOT_TOKEN)
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=response_text,
        )
    except SlackApiError:
        logger.exception("Error posting Claude response to Slack")


@csrf_exempt
@require_POST
def slack_events(request):
    logger.warning("Incoming request: %s", request.body[:200])

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        logger.warning("Failed to parse JSON body")
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    logger.info("Payload type: %s", payload.get("type"))

    # Slack URL verification handshake — handled before sig check (safe: one-time setup only)
    if payload.get("type") == "url_verification":
        logger.info("Handling url_verification challenge")
        return JsonResponse({"challenge": payload.get("challenge")})

    logger.info("Verifying Slack signature...")
    sig_valid = verify_slack_signature(request)
    logger.info("Signature valid: %s", sig_valid)
    if not sig_valid:
        return JsonResponse({"error": "Invalid signature"}, status=403)

    # Handle app_mention events
    if payload.get("type") == "event_callback":
        event = payload.get("event", {})
        if event.get("type") == "app_mention":
            raw_text = event.get("text", "")
            channel = event.get("channel", "")
            thread_ts = event.get("thread_ts") or event.get("ts", "")

            # Strip the bot mention (<@UXXXXXXXX>) from the start of the message
            user_text = re.sub(r"<@[A-Z0-9]+>", "", raw_text).strip()

            logger.info("app_mention detected in channel %s, thread_ts %s, text: %r", channel, thread_ts, user_text)

            threading.Thread(
                target=_process_mention,
                args=(user_text, channel, thread_ts),
                daemon=True,
            ).start()

    return JsonResponse({"ok": True})
