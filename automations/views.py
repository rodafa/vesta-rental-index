"""
Automations views — webhook receivers for external form submissions.
"""

import hmac
import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .services import handle_onboard_submission

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def onboard_submit(request):
    """POST /api/onboard/submit/ — receive Google Form relay, notify Slack."""
    # --- Auth: constant-time shared-secret check ---
    expected = settings.ONBOARD_SHARED_SECRET
    provided = request.headers.get("X-Vesta-Secret", "")

    if not expected or not hmac.compare_digest(expected, provided):
        logger.warning("onboard_auth_failed")
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=401)

    # --- Parse body ---
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        logger.warning("onboard_invalid_json")
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    # --- Handle ---
    try:
        handle_onboard_submission(payload)
    except Exception:
        logger.exception("onboard_slack_send_error")
        return JsonResponse(
            {"ok": False, "error": "Upstream delivery failed"}, status=502,
        )

    return JsonResponse({"ok": True})
