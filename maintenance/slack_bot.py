import hashlib
import hmac
import logging
import time

from django.conf import settings

logger = logging.getLogger(__name__)


def verify_slack_signature(request):
    """
    Verify that an incoming request is genuinely from Slack using HMAC-SHA256.
    Returns True if valid, False otherwise.
    """
    signing_secret = getattr(settings, "SLACK_SIGNING_SECRET", "")
    if not signing_secret:
        logger.warning("SLACK_SIGNING_SECRET is not set")
        return False

    logger.warning("Signing secret length: %d, first 4 chars: %s", len(signing_secret), signing_secret[:4])

    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    slack_signature = request.headers.get("X-Slack-Signature", "")

    if not timestamp or not slack_signature:
        logger.warning("Missing headers: timestamp=%r, signature=%r", timestamp, slack_signature)
        return False

    try:
        if abs(time.time() - int(timestamp)) > 300:
            logger.warning("Timestamp too old: %s", timestamp)
            return False
    except ValueError:
        return False

    body = request.body.decode("utf-8")
    sig_basestring = f"v0:{timestamp}:{body}"
    computed = "v0=" + hmac.new(
        signing_secret.encode("utf-8"),
        sig_basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    logger.warning("Computed: %s", computed)
    logger.warning("Received: %s", slack_signature)
    logger.warning("Match: %s", hmac.compare_digest(computed, slack_signature))

    return hmac.compare_digest(computed, slack_signature)
