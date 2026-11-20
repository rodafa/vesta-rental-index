"""Slack Events API endpoint for KeyBot."""

import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from keys.replies import (
    format_help,
    format_not_found,
    format_tag,
    format_unit,
    format_unit_list,
)
from keys.selectors import (
    find_tag_by_key_id,
    find_units,
    parse_query,
    tag_info,
    unit_key_info,
)
from keys.slack import post_message, strip_mention, verify_slack_signature

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
            query = strip_mention(text)
            parsed = parse_query(query)

            try:
                if parsed["type"] in ("empty", "help"):
                    reply = format_help()
                    outcome, match_count = "help", 0
                elif parsed["type"] == "key_id":
                    tag = find_tag_by_key_id(parsed["value"])
                    if tag:
                        reply = format_tag(tag_info(tag))
                        outcome, match_count = "tag_found", 1
                    else:
                        reply = format_not_found(parsed["value"])
                        outcome, match_count = "not_found", 0
                else:
                    units = find_units(parsed["value"])
                    match_count = len(units)
                    if not units:
                        reply = format_not_found(parsed["value"])
                        outcome = "not_found"
                    elif len(units) == 1:
                        reply = format_unit(unit_key_info(units[0]))
                        outcome = "unit_found"
                    else:
                        infos = [unit_key_info(u) for u in units]
                        reply = format_unit_list(infos)
                        outcome = "multi_match"
            except Exception:
                logger.exception("keybot_lookup_error")
                reply = "Something went wrong with that lookup \u2014 please try again."
                outcome, match_count = "error", 0

            logger.info(
                f"keybot_lookup query_len={len(query)}"
                f" query_type={parsed['type']}"
                f" match_count={match_count}"
                f" outcome={outcome}"
            )
            post_message(channel, reply, thread_ts=event.get("ts"))
            return JsonResponse({"ok": True})

    logger.info("keybot_unhandled_event", extra={"type": event_type})
    return JsonResponse({"ok": True})
