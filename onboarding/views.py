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

from onboarding.services.minerva import handle_mention
from onboarding.services.slack import verify_minerva_signature

logger = logging.getLogger(__name__)


def _process_mention(user_text, channel, thread_ts):
    try:
        response_text = handle_mention(user_text)
    except Exception:
        logger.exception("Error calling Claude for Minerva mention")
        response_text = "Sorry, I ran into an error. Please try again."

    try:
        client = WebClient(token=settings.MINERVA_BOT_TOKEN)
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=response_text,
        )
    except SlackApiError:
        logger.exception("Error posting Minerva response to Slack")


@csrf_exempt
@require_POST
def minerva_events(request):
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    # URL verification — before sig check (safe: one-time setup only)
    if payload.get("type") == "url_verification":
        return JsonResponse({"challenge": payload.get("challenge")})

    if not verify_minerva_signature(request):
        return JsonResponse({"error": "Invalid signature"}, status=403)

    if payload.get("type") == "event_callback":
        event = payload.get("event", {})

        # Ignore bot messages to prevent loops
        if event.get("bot_id"):
            return JsonResponse({"ok": True})

        if event.get("type") == "app_mention":
            raw_text = event.get("text", "")
            channel = event.get("channel", "")
            thread_ts = event.get("thread_ts") or event.get("ts", "")
            user_text = re.sub(r"<@[A-Z0-9]+>", "", raw_text).strip()

            logger.info("Minerva mention in channel %s: %r", channel, user_text)

            threading.Thread(
                target=_process_mention,
                args=(user_text, channel, thread_ts),
                daemon=True,
            ).start()

    return JsonResponse({"ok": True})
