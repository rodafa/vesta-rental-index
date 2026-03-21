from django.conf import settings
from slack_sdk.signature import SignatureVerifier


def verify_slack_signature(request):
    """
    Verify that an incoming request is genuinely from Slack.
    Uses slack_sdk's SignatureVerifier (HMAC-SHA256).
    Returns True if valid, False otherwise.
    """
    signing_secret = getattr(settings, "SLACK_SIGNING_SECRET", "")
    if not signing_secret:
        return False

    verifier = SignatureVerifier(signing_secret)
    return verifier.is_valid_request(request.body.decode("utf-8"), request.headers)
