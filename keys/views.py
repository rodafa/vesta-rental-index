"""Slack Events API + Interactive Components endpoints for KeyBot."""

import json
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from keys.checkouts import checkout_tag, return_tag, snooze_reminder
from keys.models import AccessNote, Checkout, PendingKeyChange, Tag
from keys.replies import (
    confirm_cancel_blocks,
    format_checkout_ok,
    format_did_you_mean,
    format_help,
    format_key_health,
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
    key_health,
    parse_query,
    tag_info,
    unit_key_info,
)
from keys.services import normalize_address
from keys.slack import (
    get_display_name,
    post_ephemeral,
    post_message,
    strip_mention,
    update_message,
    verify_slack_signature,
)
from keys.updates import (
    apply_assign,
    apply_found,
    apply_mark_missing,
    apply_retire,
    apply_set_code,
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

                elif parsed["type"] == "key_health":
                    health_data = key_health()
                    reply, blocks = format_key_health(health_data)
                    outcome, match_count = "key_health", 0

                elif parsed["type"] in _UPDATE_TYPES:
                    _handle_update(parsed, event, channel)
                    outcome, match_count = parsed["type"], 0

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
            except Exception as exc:
                logger.exception(
                    f"keybot_lookup_error query_type={parsed['type']}"
                    f" error={exc}"
                )
                reply = "Something went wrong \u2014 please try again."
                blocks = None
                outcome, match_count = "error", 0

            logger.info(
                f"keybot_lookup query_len={len(query)}"
                f" query_type={parsed['type']}"
                f" match_count={match_count}"
                f" outcome={outcome}"
            )
            if reply is not None:
                try:
                    post_message(
                        channel, reply,
                        thread_ts=event.get("ts"), blocks=blocks,
                    )
                except Exception as exc:
                    logger.exception(
                        f"keybot_reply_error query_type={parsed['type']}"
                        f" error={exc}"
                    )
                    _safe_reply(channel, reply, event.get("ts"))
            return JsonResponse({"ok": True})

    logger.info("keybot_unhandled_event", extra={"type": event_type})
    return JsonResponse({"ok": True})


def _safe_reply(channel, text, thread_ts):
    """Post a plain-text reply, swallowing errors. Last-resort fallback."""
    try:
        post_message(channel, text, thread_ts=thread_ts)
    except Exception as exc:
        logger.exception(f"keybot_safe_reply_failed error={exc}")


# ── Update-command constants ────────────────────────────────────────

_UPDATE_TYPES = frozenset({
    "set_code", "assign", "retire", "mark_missing", "found",
})

_APPLY_DISPATCH = {
    "set_code": apply_set_code,
    "assign": apply_assign,
    "retire": apply_retire,
    "mark_missing": apply_mark_missing,
    "found": apply_found,
}


def _is_editor(slack_user_id: str) -> bool:
    """True when the Slack user is in the KEYBOT_EDITORS allowlist."""
    return slack_user_id in settings.KEYBOT_EDITORS


def _tag_location(tag) -> str:
    """Human-readable location for a tag (address or 'unassigned')."""
    if tag.unit:
        return tag.unit.display_address
    if tag.property:
        return tag.property.address_line_1 or "unknown"
    return "unassigned"


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


# ── Update sub-handlers ────────────────────────────────────────────


def _handle_update(parsed: dict, event: dict, channel: str):
    """Dispatch an update command. Posts directly (never sets reply)."""
    user_id = event.get("user", "")
    thread_ts = event.get("ts")

    if not _is_editor(user_id):
        post_ephemeral(
            channel, user_id,
            "You don't have permission to make changes via KeyBot.",
        )
        return

    handler = {
        "set_code": _handle_set_code,
        "assign": _handle_assign,
        "retire": _handle_retire,
        "mark_missing": _handle_mark_missing,
        "found": _handle_found,
    }.get(parsed["type"])

    if handler:
        handler(parsed["value"], user_id, channel, thread_ts)


def _handle_set_code(value: dict, user_id: str, channel: str, thread_ts: str):
    """Set code: resolve address, preview, create PendingKeyChange."""
    address_text = value["address"]
    code = value["code"]

    result = find_units(address_text)
    units = result["units"]
    if not units:
        post_message(
            channel, format_not_found(address_text), thread_ts=thread_ts,
        )
        return
    if len(units) > 1:
        # Multiple properties or multiple units — user must be more specific.
        # Check if all units share one property (multi-unit building).
        props = {u.property_id for u in units}
        if len(props) == 1:
            # Multi-unit, same property. Check if the query had a unit token.
            cleaned = address_text.strip()
            _sn, _bn, unit_token = normalize_address(cleaned)
            if not unit_token:
                # No unit token → property-level code.
                prop = units[0].property
                current = AccessNote.objects.filter(
                    property=prop, unit__isnull=True,
                ).order_by("-source_date", "-created_at").first()
                current_text = (
                    f"Current: `{current.text}`" if current else "No code on file"
                )
                display_name = get_display_name(user_id)
                preview = (
                    f"Set *property-level* code for"
                    f" *{prop.address_line_1}* to `{code}`\n"
                    f"{current_text}"
                )
                pending = PendingKeyChange.objects.create(
                    requester_slack_id=user_id,
                    requester_name=display_name,
                    action="set_code",
                    payload={
                        "unit_id": None,
                        "property_id": prop.pk,
                        "code": code,
                    },
                )
                text, blocks = confirm_cancel_blocks(preview, pending.pk)
                post_message(channel, text, thread_ts=thread_ts, blocks=blocks)
                logger.info(
                    f"keybot_set_code_preview pending={pending.pk}"
                    f" property={prop.pk} by={user_id}"
                )
                return
            # Has unit token but multiple units — ambiguous.
            pass
        post_message(
            channel,
            f"Multiple matches for \"{address_text}\" — add a unit number.",
            thread_ts=thread_ts,
        )
        return

    unit = units[0]
    prop = unit.property
    current = AccessNote.objects.filter(
        Q(unit=unit) | Q(property=prop, unit__isnull=True),
    ).order_by("-source_date", "-created_at").first()
    current_text = (
        f"Current: `{current.text}`" if current else "No code on file"
    )
    display_name = get_display_name(user_id)
    preview = (
        f"Set code for *{unit.display_address}* to `{code}`\n"
        f"{current_text}"
    )
    pending = PendingKeyChange.objects.create(
        requester_slack_id=user_id,
        requester_name=display_name,
        action="set_code",
        payload={
            "unit_id": unit.pk,
            "property_id": prop.pk,
            "code": code,
        },
    )
    text, blocks = confirm_cancel_blocks(preview, pending.pk)
    post_message(channel, text, thread_ts=thread_ts, blocks=blocks)
    logger.info(
        f"keybot_set_code_preview pending={pending.pk}"
        f" unit={unit.pk} by={user_id}"
    )


def _handle_assign(value: dict, user_id: str, channel: str, thread_ts: str):
    """Assign tag to unit: resolve both, preview, create PendingKeyChange."""
    key_id = value["key_id"]
    address_text = value["address"]

    tag = find_tag_by_key_id(key_id)
    if tag is None:
        post_message(
            channel, format_not_found(key_id), thread_ts=thread_ts,
        )
        return
    if tag.status == "retired":
        post_message(
            channel,
            f"{key_id} is retired and cannot be assigned.",
            thread_ts=thread_ts,
        )
        return

    result = find_units(address_text)
    units = result["units"]
    if not units:
        post_message(
            channel, format_not_found(address_text), thread_ts=thread_ts,
        )
        return
    if len(units) > 1:
        post_message(
            channel,
            f"Multiple matches for \"{address_text}\" — add a unit number.",
            thread_ts=thread_ts,
        )
        return

    unit = units[0]
    display_name = get_display_name(user_id)

    # Check for existing tags on target unit (duplicate warning)
    existing = list(
        Tag.objects.filter(unit=unit)
        .exclude(status="retired")
        .exclude(pk=tag.pk)
        .values_list("key_id", flat=True)
    )
    warning = ""
    if existing:
        ids = ", ".join(existing)
        warning = f"\n\u26a0\ufe0f Unit already has: {ids}"

    old_loc = _tag_location(tag) if tag.unit or tag.property else "unassigned"
    preview = (
        f"Assign *{key_id}* to *{unit.display_address}*\n"
        f"Currently: {old_loc}{warning}"
    )
    pending = PendingKeyChange.objects.create(
        requester_slack_id=user_id,
        requester_name=display_name,
        action="assign",
        payload={"tag_id": tag.pk, "unit_id": unit.pk},
    )
    text, blocks = confirm_cancel_blocks(preview, pending.pk)
    post_message(channel, text, thread_ts=thread_ts, blocks=blocks)
    logger.info(
        f"keybot_assign_preview pending={pending.pk}"
        f" tag={key_id} unit={unit.pk} by={user_id}"
    )


def _handle_retire(value: dict, user_id: str, channel: str, thread_ts: str):
    """Retire tag: resolve, preview, create PendingKeyChange."""
    key_id = value["key_id"]
    tag = find_tag_by_key_id(key_id)
    if tag is None:
        post_message(
            channel, format_not_found(key_id), thread_ts=thread_ts,
        )
        return
    if tag.status == "retired":
        post_message(
            channel, f"{key_id} is already retired.", thread_ts=thread_ts,
        )
        return
    if tag.status == "checked_out":
        post_message(
            channel,
            f"{key_id} is currently checked out \u2014 return it first.",
            thread_ts=thread_ts,
        )
        return

    display_name = get_display_name(user_id)
    loc = _tag_location(tag)
    preview = f"Retire *{key_id}*\nCurrently: {loc} ({tag.get_status_display().lower()})"
    pending = PendingKeyChange.objects.create(
        requester_slack_id=user_id,
        requester_name=display_name,
        action="retire",
        payload={"tag_id": tag.pk},
    )
    text, blocks = confirm_cancel_blocks(preview, pending.pk)
    post_message(channel, text, thread_ts=thread_ts, blocks=blocks)
    logger.info(
        f"keybot_retire_preview pending={pending.pk}"
        f" tag={key_id} by={user_id}"
    )


def _handle_mark_missing(value: dict, user_id: str, channel: str, thread_ts: str):
    """Mark tag missing: resolve, preview, create PendingKeyChange."""
    key_id = value["key_id"]
    tag = find_tag_by_key_id(key_id)
    if tag is None:
        post_message(
            channel, format_not_found(key_id), thread_ts=thread_ts,
        )
        return
    if tag.status == "retired":
        post_message(
            channel, f"{key_id} is retired.", thread_ts=thread_ts,
        )
        return
    if tag.status == "missing":
        post_message(
            channel, f"{key_id} is already marked missing.", thread_ts=thread_ts,
        )
        return

    display_name = get_display_name(user_id)
    loc = _tag_location(tag)
    checkout_note = ""
    if tag.status == "checked_out":
        co = Checkout.objects.filter(
            tag=tag, returned_at__isnull=True,
        ).first()
        if co:
            checkout_note = (
                f"\n\u26a0\ufe0f Currently checked out to"
                f" {co.checked_out_by_name} \u2014 checkout will be closed"
            )
    preview = (
        f"Mark *{key_id}* as missing\n"
        f"Currently: {loc} ({tag.get_status_display().lower()})"
        f"{checkout_note}"
    )
    pending = PendingKeyChange.objects.create(
        requester_slack_id=user_id,
        requester_name=display_name,
        action="mark_missing",
        payload={"tag_id": tag.pk},
    )
    text, blocks = confirm_cancel_blocks(preview, pending.pk)
    post_message(channel, text, thread_ts=thread_ts, blocks=blocks)
    logger.info(
        f"keybot_mark_missing_preview pending={pending.pk}"
        f" tag={key_id} by={user_id}"
    )


def _handle_found(value: dict, user_id: str, channel: str, thread_ts: str):
    """Mark tag found: resolve, preview, create PendingKeyChange."""
    key_id = value["key_id"]
    tag = find_tag_by_key_id(key_id)
    if tag is None:
        post_message(
            channel, format_not_found(key_id), thread_ts=thread_ts,
        )
        return
    if tag.status != "missing":
        post_message(
            channel,
            f"{key_id} is not marked missing"
            f" (current: {tag.get_status_display().lower()}).",
            thread_ts=thread_ts,
        )
        return

    display_name = get_display_name(user_id)
    loc = _tag_location(tag)
    preview = f"Mark *{key_id}* as found (on hook)\nCurrently: {loc} (missing)"
    pending = PendingKeyChange.objects.create(
        requester_slack_id=user_id,
        requester_name=display_name,
        action="found",
        payload={"tag_id": tag.pk},
    )
    text, blocks = confirm_cancel_blocks(preview, pending.pk)
    post_message(channel, text, thread_ts=thread_ts, blocks=blocks)
    logger.info(
        f"keybot_found_preview pending={pending.pk}"
        f" tag={key_id} by={user_id}"
    )


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
        user_obj = payload.get("user", {})
        user_id = user_obj.get("id", "")
        fallback_name = (
            user_obj.get("username") or user_obj.get("name") or user_id
        )
        channel_id = payload.get("channel", {}).get("id", "")
        message_ts = payload.get("message", {}).get("ts", "")

        try:
            if action_id.startswith("keybot_take:"):
                _interaction_take(
                    value, user_id, fallback_name, channel_id, message_ts,
                )
            elif action_id.startswith("keybot_return:"):
                _interaction_lookup_return(
                    value, user_id, fallback_name, channel_id, message_ts,
                )
            elif action_id == "keybot_reminder_return":
                _interaction_reminder_return(
                    value, user_id, fallback_name, channel_id, message_ts,
                )
            elif action_id == "keybot_snooze":
                _interaction_reminder_snooze(
                    value, user_id, fallback_name, channel_id, message_ts,
                )
            elif action_id.startswith("keybot_confirm:"):
                _interaction_confirm(
                    value, user_id, channel_id, message_ts,
                )
            elif action_id.startswith("keybot_cancel:"):
                _interaction_cancel(
                    value, user_id, channel_id, message_ts,
                )
        except Exception as exc:
            logger.exception(
                f"keybot_interaction_error action={action_id} value={value}"
                f" error={exc}"
            )
            try:
                post_ephemeral(
                    channel_id, user_id,
                    "Something went wrong with that button \u2014 please try again.",
                )
            except Exception as exc2:
                logger.exception(
                    f"keybot_interaction_error_ephemeral_failed error={exc2}"
                )

    return HttpResponse(status=200)


# ── Interaction sub-handlers ────────────────────────────────────────


def _interaction_take(key_id, user_id, fallback_name, channel_id, message_ts):
    """Take button pressed on a lookup message."""
    tag = find_tag_by_key_id(key_id)
    if tag is None:
        post_ephemeral(channel_id, user_id, f"Tag {key_id} not found.")
        return

    display_name = get_display_name(user_id, fallback=fallback_name)
    result = checkout_tag(tag, user_id, display_name)
    if isinstance(result, str):
        post_ephemeral(channel_id, user_id, result)
        return

    new_text, new_blocks = _rebuild_lookup_message(tag)
    try:
        update_message(channel_id, message_ts, new_text, blocks=new_blocks)
    except Exception as exc:
        logger.exception(
            f"keybot_update_message_failed action=take tag={key_id} error={exc}"
        )
    confirm = format_checkout_ok(key_id, display_name)
    _safe_reply(channel_id, confirm, message_ts)
    logger.info(f"keybot_button_take tag={key_id} by={user_id}")


def _interaction_lookup_return(key_id, user_id, fallback_name, channel_id, message_ts):
    """Return button pressed on a lookup message."""
    tag = find_tag_by_key_id(key_id)
    if tag is None:
        post_ephemeral(channel_id, user_id, f"Tag {key_id} not found.")
        return

    closed = return_tag(tag, user_id)
    if closed is None:
        post_ephemeral(channel_id, user_id, f"{key_id} is not currently checked out.")
        return

    display_name = get_display_name(user_id, fallback=fallback_name)
    new_text, new_blocks = _rebuild_lookup_message(tag)
    try:
        update_message(channel_id, message_ts, new_text, blocks=new_blocks)
    except Exception as exc:
        logger.exception(
            f"keybot_update_message_failed action=return tag={key_id}"
            f" error={exc}"
        )
    confirm = f"\u21a9\ufe0f *{key_id}* returned by {display_name}."
    _safe_reply(channel_id, confirm, message_ts)
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


def _interaction_reminder_return(checkout_id_str, user_id, fallback_name, channel_id, message_ts):
    """Return button on a reminder message. Anyone can press this."""
    try:
        co = Checkout.objects.select_related("tag").get(pk=checkout_id_str)
    except Checkout.DoesNotExist:
        logger.warning(f"keybot_interaction_missing_checkout id={checkout_id_str}")
        return

    return_tag(co.tag, user_id)
    display_name = get_display_name(user_id, fallback=fallback_name)
    new_text = reminder_resolved_text(co.tag.key_id, "returned", display_name)
    try:
        update_message(channel_id, message_ts, new_text)
    except Exception as exc:
        logger.exception(
            f"keybot_update_message_failed action=reminder_return"
            f" tag={co.tag.key_id} error={exc}"
        )
        _safe_reply(channel_id, new_text, message_ts)
    logger.info(f"keybot_button_reminder_return tag={co.tag.key_id} by={user_id}")


def _interaction_reminder_snooze(checkout_id_str, user_id, fallback_name, channel_id, message_ts):
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
    display_name = get_display_name(user_id, fallback=fallback_name)
    new_text = reminder_resolved_text(co.tag.key_id, "snoozed", display_name)
    try:
        update_message(channel_id, message_ts, new_text)
    except Exception as exc:
        logger.exception(
            f"keybot_update_message_failed action=snooze"
            f" tag={co.tag.key_id} error={exc}"
        )
        _safe_reply(channel_id, new_text, message_ts)
    logger.info(f"keybot_button_snooze tag={co.tag.key_id} by={user_id}")


# ── Confirm / Cancel interaction handlers ──────────────────────────

_CONFIRM_EXPIRY = timedelta(minutes=15)


def _interaction_confirm(pending_id_str, user_id, channel_id, message_ts):
    """Confirm button on a pending change. Applies inside transaction.

    All Slack calls happen AFTER the transaction closes so that:
    (a) the row lock is never held during a network call, and
    (b) a Slack failure cannot roll back a DB change that should stand.
    """
    now = timezone.now()

    # Phase 1: DB work inside transaction — collect result, no Slack calls.
    with transaction.atomic():
        try:
            pending = PendingKeyChange.objects.select_for_update().get(
                pk=pending_id_str,
            )
        except PendingKeyChange.DoesNotExist:
            pending = None

        if pending is None:
            result = ("ephemeral", "Change not found.")
        elif pending.resolved_at is not None:
            result = ("ephemeral", f"Already {pending.outcome}.")
        elif now - pending.created_at > _CONFIRM_EXPIRY:
            pending.resolved_at = now
            pending.outcome = "expired"
            pending.save(update_fields=["resolved_at", "outcome"])
            result = ("update", "\u23f0 Expired \u2014 please re-run the command.")
        elif pending.requester_slack_id != user_id:
            result = ("ephemeral", f"Only <@{pending.requester_slack_id}> can confirm this.")
        else:
            apply_fn = _APPLY_DISPATCH.get(pending.action)
            if not apply_fn:
                logger.error(
                    f"keybot_confirm_unknown_action pending={pending.pk}"
                    f" action={pending.action}"
                )
                result = ("ephemeral", "Unknown action type.")
            else:
                error = apply_fn(pending)
                if error:
                    result = ("ephemeral", error)
                else:
                    pending.resolved_at = now
                    pending.outcome = "confirmed"
                    pending.save(update_fields=["resolved_at", "outcome"])
                    result = ("confirmed", None)

    # Phase 2: Slack calls outside transaction.
    kind, msg = result
    if kind == "ephemeral":
        post_ephemeral(channel_id, user_id, msg)
    elif kind == "update":
        try:
            update_message(channel_id, message_ts, msg)
        except Exception as exc:
            logger.exception(
                f"keybot_update_message_failed action=confirm"
                f" pending={pending_id_str} error={exc}"
            )
    elif kind == "confirmed":
        try:
            update_message(channel_id, message_ts, "\u2705 Done.")
        except Exception as exc:
            logger.exception(
                f"keybot_update_message_failed action=confirm"
                f" pending={pending_id_str} error={exc}"
            )
        logger.info(
            f"keybot_confirmed pending={pending_id_str}"
            f" action={pending.action} by={user_id}"
        )


def _interaction_cancel(pending_id_str, user_id, channel_id, message_ts):
    """Cancel button on a pending change.

    All Slack calls happen AFTER the transaction closes.
    """
    now = timezone.now()

    with transaction.atomic():
        try:
            pending = PendingKeyChange.objects.select_for_update().get(
                pk=pending_id_str,
            )
        except PendingKeyChange.DoesNotExist:
            pending = None

        if pending is None:
            result = ("ephemeral", "Change not found.")
        elif pending.resolved_at is not None:
            result = ("ephemeral", f"Already {pending.outcome}.")
        elif pending.requester_slack_id != user_id:
            result = ("ephemeral", f"Only <@{pending.requester_slack_id}> can cancel this.")
        else:
            pending.resolved_at = now
            pending.outcome = "cancelled"
            pending.save(update_fields=["resolved_at", "outcome"])
            result = ("cancelled", None)

    kind, msg = result
    if kind == "ephemeral":
        post_ephemeral(channel_id, user_id, msg)
    elif kind == "cancelled":
        try:
            update_message(channel_id, message_ts, "\u274c Cancelled.")
        except Exception as exc:
            logger.exception(
                f"keybot_update_message_failed action=cancel"
                f" pending={pending_id_str} error={exc}"
            )
        logger.info(
            f"keybot_cancelled pending={pending_id_str}"
            f" action={pending.action} by={user_id}"
        )
