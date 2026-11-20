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


def post_message(channel: str, text: str, thread_ts: str | None = None) -> None:
    """POST to chat.postMessage with Bearer token. Raises on Slack errors."""
    payload = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        json=payload,
        headers={"Authorization": f"Bearer {settings.SLACK_BOT_TOKEN}"},
        timeout=5,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        logger.error("slack_post_message_failed", extra={"error": data.get("error")})
        raise RuntimeError(f"Slack API error: {data.get('error')}")


def strip_mention(text: str) -> str:
    """Remove leading '<@Uxxxx>' bot mention and surrounding whitespace."""
    return _MENTION_RE.sub("", text).strip()
