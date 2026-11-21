"""Slack Events API + Interactive Components endpoints for KeyBot."""

import json
import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from keys.checkouts import checkout_tag, return_tag, snooze_reminder
from keys.models import Checkout
from keys.replies import (
    format_checkout_ok,
    format_did_you_mean,
    format_help,
    format_not_found,
    format_return_ok,
    format_tag,
    format_unit,
    format_unit_list,
    format_whats_out,
    lookup_blocks,
    reminder_resolved_text,
    take_buttons,
)
from keys.selectors import (
    find_tag_by_key_id,
    find_units,
    parse_query,
    tag_info,
    unit_key_info,
)
from keys.slack import (
    get_display_name,
    post_ephemeral,
    post_message,
    strip_mention,
    update_message,
    verify_slack_signature,
)

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
                reply = None
                blocks = None

                if parsed["type"] in ("empty", "help"):
                    reply = format_help()
                    outcome, match_count = "help", 0

                elif parsed["type"] == "take":
                    outcome, match_count = _handle_take(
                        parsed["value"], event, channel,
                    )

                elif parsed["type"] == "return":
                    outcome, match_count = _handle_return(
                        parsed["value"], event, channel,
                    )

                elif parsed["type"] == "whats_out":
                    reply = _handle_whats_out()
                    outcome, match_count = "whats_out", 0

                elif parsed["type"] == "key_id":
                    tag = find_tag_by_key_id(parsed["value"])
                    if tag:
                        info = tag_info(tag)
                        reply = format_tag(info)
                        buttons = take_buttons([info])
                        blocks = lookup_blocks(reply, buttons)
                        outcome, match_count = "tag_found", 1
                    else:
                        reply = format_not_found(parsed["value"])
                        outcome, match_count = "not_found", 0
                else:
                    result = find_units(parsed["value"])
                    units = result["units"]
                    exact = result["exact"]
                    match_count = len(units)
                    if not units:
                        reply = format_not_found(parsed["value"])
                        outcome = "not_found"
                    elif exact:
                        if len(units) == 1:
                            info = unit_key_info(units[0])
                            reply = format_unit(info)
                            all_tags = info["tags"] + info.get("property_tags", [])
                            buttons = take_buttons(all_tags)
                            blocks = lookup_blocks(reply, buttons)
                            outcome = "unit_found"
                        else:
                            infos = [unit_key_info(u) for u in units[:10]]
                            reply = format_unit_list(infos, len(units))
                            outcome = "multi_match"
                    else:
                        if result["narrowed_by_token"] and len(units) == 1:
                            info = unit_key_info(units[0])
                            reply = (
                                "_Closest match:_\n"
                                + format_unit(info)
                            )
                            all_tags = info["tags"] + info.get("property_tags", [])
                            buttons = take_buttons(all_tags)
                            blocks = lookup_blocks(reply, buttons)
                            outcome = "closest_match"
                        else:
                            infos = [unit_key_info(u) for u in units[:10]]
                            reply = format_did_you_mean(
                                parsed["value"], infos, len(units),
                            )
                            outcome = "did_you_mean"
            except Exception:
                logger.exception("keybot_lookup_error")
                reply = "Something went wrong \u2014 please try again."
                outcome, match_count = "error", 0

            logger.info(
                f"keybot_lookup query_len={len(query)}"
                f" query_type={parsed['type']}"
                f" match_count={match_count}"
                f" outcome={outcome}"
            )
            if reply is not None:
                post_message(
                    channel, reply,
                    thread_ts=event.get("ts"), blocks=blocks,
                )
            return JsonResponse({"ok": True})

    logger.info("keybot_unhandled_event", extra={"type": event_type})
    return JsonResponse({"ok": True})


# ── Mention sub-handlers ────────────────────────────────────────────


def _handle_take(key_id: str, event: dict, channel: str) -> tuple[str, int]:
    """Check out a tag. Posts reply directly, returns (outcome, count)."""
    slack_user_id = event.get("user", "")
    tag = find_tag_by_key_id(key_id)
    if tag is None:
        post_message(
            channel,
            format_not_found(key_id),
            thread_ts=event.get("ts"),
        )
        return "not_found", 0

    display_name = get_display_name(slack_user_id)
    result = checkout_tag(tag, slack_user_id, display_name)
    if isinstance(result, str):
        # Refusal message
        post_message(channel, result, thread_ts=event.get("ts"))
        return "checkout_refused", 0

    post_message(
        channel,
        format_checkout_ok(key_id, display_name),
        thread_ts=event.get("ts"),
    )
    return "checkout_ok", 1


def _handle_return(key_id: str, event: dict, channel: str) -> tuple[str, int]:
    """Return a checked-out tag. Posts reply directly, returns (outcome, count)."""
    slack_user_id = event.get("user", "")
    tag = find_tag_by_key_id(key_id)
    if tag is None:
        post_message(
            channel,
            format_not_found(key_id),
            thread_ts=event.get("ts"),
        )
        return "not_found", 0

    display_name = get_display_name(slack_user_id)
    closed = return_tag(tag, slack_user_id)
    if closed is None:
        post_message(
            channel,
            f"{key_id} is not currently checked out.",
            thread_ts=event.get("ts"),
        )
        return "return_noop", 0

    post_message(
        channel,
        format_return_ok(key_id, display_name),
        thread_ts=event.get("ts"),
    )
    return "return_ok", 1


def _handle_whats_out() -> str:
    """Build the 'what's out' reply string."""
    from keys.checkouts import open_checkouts

    qs = open_checkouts()
    cos = [
        {
            "key_id": co.tag.key_id,
            "checked_out_by_name": co.checked_out_by_name,
            "checked_out_by_slack_id": co.checked_out_by_slack_id,
            "checked_out_at": co.checked_out_at,
        }
        for co in qs
    ]
    return format_whats_out(cos, timezone.now())


# ── Slack Interactive Components ────────────────────────────────────


@csrf_exempt
@require_POST
def slack_interactions(request):
    """Handle button presses from Block Kit messages."""
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    if not verify_slack_signature(
        settings.SLACK_SIGNING_SECRET, timestamp, request.body, signature
    ):
        logger.warning("keybot_interactions_invalid_signature")
        return HttpResponse(status=401)

    payload = json.loads(request.POST.get("payload", "{}"))

    for action in payload.get("actions", []):
        action_id = action.get("action_id", "")
        value = action.get("value", "")
        user_id = payload.get("user", {}).get("id", "")
        channel_id = payload.get("channel", {}).get("id", "")
        message_ts = payload.get("message", {}).get("ts", "")

        try:
            if action_id == "keybot_take":
                _interaction_take(value, user_id, channel_id, message_ts)
            elif action_id == "keybot_return":
                _interaction_lookup_return(value, user_id, channel_id, message_ts)
            elif action_id == "keybot_reminder_return":
                _interaction_reminder_return(
                    value, user_id, channel_id, message_ts,
                )
            elif action_id == "keybot_snooze":
                _interaction_reminder_snooze(
                    value, user_id, channel_id, message_ts,
                )
        except Exception:
            logger.exception(
                f"keybot_interaction_error action={action_id} value={value}"
            )
            try:
                post_ephemeral(
                    channel_id, user_id,
                    "Something went wrong with that button \u2014 please try again.",
                )
            except Exception:
                logger.exception("keybot_interaction_error_ephemeral_failed")

    return HttpResponse(status=200)


# ── Interaction sub-handlers ────────────────────────────────────────


def _interaction_take(key_id, user_id, channel_id, message_ts):
    """Take button pressed on a lookup message."""
    tag = find_tag_by_key_id(key_id)
    if tag is None:
        post_ephemeral(channel_id, user_id, f"Tag {key_id} not found.")
        return

    display_name = get_display_name(user_id)
    result = checkout_tag(tag, user_id, display_name)
    if isinstance(result, str):
        post_ephemeral(channel_id, user_id, result)
        return

    new_text, new_blocks = _rebuild_lookup_message(tag)
    update_message(channel_id, message_ts, new_text, blocks=new_blocks)
    post_message(
        channel_id, format_checkout_ok(key_id, display_name),
        thread_ts=message_ts,
    )
    logger.info(f"keybot_button_take tag={key_id} by={user_id}")


def _interaction_lookup_return(key_id, user_id, channel_id, message_ts):
    """Return button pressed on a lookup message."""
    tag = find_tag_by_key_id(key_id)
    if tag is None:
        post_ephemeral(channel_id, user_id, f"Tag {key_id} not found.")
        return

    closed = return_tag(tag, user_id)
    if closed is None:
        post_ephemeral(channel_id, user_id, f"{key_id} is not currently checked out.")
        return

    display_name = get_display_name(user_id)
    new_text, new_blocks = _rebuild_lookup_message(tag)
    update_message(channel_id, message_ts, new_text, blocks=new_blocks)
    post_message(
        channel_id,
        f"\u21a9\ufe0f *{key_id}* returned by {display_name}.",
        thread_ts=message_ts,
    )
    logger.info(f"keybot_button_return_lookup tag={key_id} by={user_id}")


def _rebuild_lookup_message(tag):
    """Re-render a lookup message after a take/return button press.

    Returns (text, blocks).  Always includes blocks so chat.update
    replaces old buttons with the current state.
    """
    tag.refresh_from_db()
    if tag.unit:
        info = unit_key_info(tag.unit)
        text = format_unit(info)
        all_tags = info["tags"] + info.get("property_tags", [])
    else:
        info = tag_info(tag)
        text = format_tag(info)
        all_tags = [info]

    buttons = take_buttons(all_tags)
    if buttons:
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {"type": "actions", "elements": buttons},
        ]
    else:
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    return text, blocks


def _interaction_reminder_return(checkout_id_str, user_id, channel_id, message_ts):
    """Return button on a reminder message. Anyone can press this."""
    try:
        co = Checkout.objects.select_related("tag").get(pk=checkout_id_str)
    except Checkout.DoesNotExist:
        logger.warning(f"keybot_interaction_missing_checkout id={checkout_id_str}")
        return

    return_tag(co.tag, user_id)
    display_name = get_display_name(user_id)
    new_text = reminder_resolved_text(co.tag.key_id, "returned", display_name)
    update_message(channel_id, message_ts, new_text)
    logger.info(f"keybot_button_reminder_return tag={co.tag.key_id} by={user_id}")


def _interaction_reminder_snooze(checkout_id_str, user_id, channel_id, message_ts):
    """Snooze button on a reminder message. Holder-only."""
    try:
        co = Checkout.objects.select_related("tag").get(pk=checkout_id_str)
    except Checkout.DoesNotExist:
        logger.warning(f"keybot_interaction_missing_checkout id={checkout_id_str}")
        return

    if co.checked_out_by_slack_id != user_id:
        post_ephemeral(
            channel_id, user_id,
            f"Only {co.checked_out_by_name} can snooze this reminder.",
        )
        return

    snooze_reminder(co)
    display_name = get_display_name(user_id)
    new_text = reminder_resolved_text(co.tag.key_id, "snoozed", display_name)
    update_message(channel_id, message_ts, new_text)
    logger.info(f"keybot_button_snooze tag={co.tag.key_id} by={user_id}")
