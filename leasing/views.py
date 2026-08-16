"""
Webhook receiver for RentEngine.
"""

import hmac
import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import RentEngineWebhookDelivery
from .services import process_webhook_delivery

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def rentengine_webhook(request):
    """POST /api/webhooks/rentengine/ — receive RentEngine webhook deliveries."""
    # --- Auth: constant-time API-key check ---
    expected = settings.RENTENGINE_WEBHOOK_SECRET
    provided = request.headers.get("X-API-Key", "")

    if not expected or not hmac.compare_digest(expected, provided):
        logger.warning("rentengine_webhook_auth_failed")
        return JsonResponse({"ok": False, "error": "Unauthorized"}, status=401)

    # --- Body size guard ---
    if len(request.body) > 1_000_000:
        logger.warning("rentengine_webhook_body_too_large")
        return JsonResponse({"ok": True})

    # --- Parse body and store delivery BEFORE processing ---
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        # Still store so the payload is never lost
        delivery = RentEngineWebhookDelivery.objects.create(
            raw_payload={"_raw_body": request.body.decode("utf-8", errors="replace")[:100000]},
            status="unparseable",
            error_message="Request body is not valid JSON",
        )
        logger.info(
            "rentengine_webhook_received",
            extra={
                "delivery_id": delivery.pk,
                "entity": "",
                "operation": "",
                "status": "unparseable",
            },
        )
        return JsonResponse({"ok": True})

    # Store raw delivery before any processing
    delivery = RentEngineWebhookDelivery.objects.create(
        raw_payload=payload,
        status="unparseable",  # safe default; process_webhook_delivery updates it
    )

    # --- Process ---
    process_webhook_delivery(delivery)
    delivery.refresh_from_db()

    logger.info(
        "rentengine_webhook_received",
        extra={
            "delivery_id": delivery.pk,
            "entity": delivery.target_entity,
            "operation": delivery.operation,
            "status": delivery.status,
        },
    )

    return JsonResponse({"ok": True})
