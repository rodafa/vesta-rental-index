"""Slack helpers for KeyBot — signature verification and message posting."""

import hashlib
import hmac
import logging
import re
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"^<@U[A-Z0-9]+>\s*")


def verify_slack_signature(
    signing_secret: str, timestamp: str, body: bytes, signature: str
) -> bool:
    """HMAC-SHA256 verification of Slack request signature (v0 scheme).

    Returns False if the secret is empty, the timestamp is stale (>300 s),
    or the signature does not match.
    """
    if not signing_secret:
        return False
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False
    if abs(time.time() - ts) > 300:
        return False
    basestring = f"v0:{timestamp}:".encode() + body
    expected = "v0=" + hmac.new(
        signing_secret.encode(), basestring, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def post_message(
    channel: str,
    text: str,
    thread_ts: str | None = None,
    blocks: list | None = None,
) -> dict:
    """POST to chat.postMessage. Returns the Slack response dict.

    ``text`` is the fallback / notification string.
    ``blocks`` (optional) is a list of Block Kit block dicts.
    """
    payload = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    if blocks is not None:
        payload["blocks"] = blocks
    return _slack_api("chat.postMessage", payload)


def update_message(channel: str, ts: str, text: str, blocks: list | None = None) -> dict:
    """Update an existing message via chat.update."""
    payload = {"channel": channel, "ts": ts, "text": text}
    if blocks is not None:
        payload["blocks"] = blocks
    return _slack_api("chat.update", payload)


def post_ephemeral(channel: str, user: str, text: str) -> dict:
    """Send an ephemeral message visible only to *user*."""
    return _slack_api("chat.postEphemeral", {
        "channel": channel,
        "user": user,
        "text": text,
    })


def get_display_name(user_id: str, fallback: str = "") -> str:
    """Fetch a Slack user's display name. Never raises.

    Falls back to *fallback* (e.g. payload username), then *user_id*.
    """
    try:
        data = _slack_api("users.info", {"user": user_id})
        profile = data.get("user", {}).get("profile", {})
        return (
            profile.get("display_name")
            or profile.get("real_name")
            or fallback
            or user_id
        )
    except Exception as exc:
        logger.warning(
            f"slack_display_name_failed user={user_id} error={exc}"
        )
        return fallback or user_id


def _slack_api(method: str, payload: dict) -> dict:
    """POST to a Slack Web API method. Returns parsed response or raises."""
    resp = requests.post(
        f"https://slack.com/api/{method}",
        json=payload,
        headers={"Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}"},
        timeout=5,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        logger.error(f"slack_{method}_failed error={data.get('error')}")
        raise RuntimeError(f"Slack API error: {data.get('error')}")
    return data


def strip_mention(text: str) -> str:
    """Remove leading '<@Uxxxx>' bot mention and surrounding whitespace."""
    return _MENTION_RE.sub("", text).strip()
