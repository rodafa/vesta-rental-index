"""
Webhook receiver endpoints for external integrations.

Mounted at /api/webhooks/ — each integration gets its own sub-path.
"""

import logging

from django.conf import settings
from django.http import HttpRequest
from ninja import Router
from ninja.security import APIKeyHeader

from integrations.models import WebhookEvent
from integrations.rentengine.processors import process_webhook_event

logger = logging.getLogger(__name__)

router = Router(tags=["Webhooks"])


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class RentEngineWebhookAuth(APIKeyHeader):
    """
    Validates X-Webhook-Secret header against the configured secret.

    If RENTENGINE.WEBHOOK_SECRET is not set (empty string), all requests
    are allowed — this supports local dev without configuring secrets.
    """

    param_name = "X-Webhook-Secret"

    def authenticate(self, request: HttpRequest, key: str | None):
        expected = settings.RENTENGINE.get("WEBHOOK_SECRET", "")
        if not expected:
            return "dev"  # No secret configured — allow all (dev mode)
        if key == expected:
            return key
        return None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/rentengine/", auth=[RentEngineWebhookAuth()], response={200: dict})
def rentengine_webhook(request: HttpRequest):
    """
    Receive a webhook event from RentEngine.

    Expected JSON body:
        {
            "type": "INSERT | UPDATE | DELETE",
            "table": "prospects | leasing_events",
            "record": { ... },
            "old_record": { ... } or null
        }
    """
    body = request.ninja_parsed_body if hasattr(request, "ninja_parsed_body") else {}
    # Django Ninja parses JSON body automatically; fall back to manual parse
    if not body:
        import json

        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            logger.warning("RentEngine webhook: invalid JSON body")
            return {"status": "error", "detail": "Invalid JSON"}

    event_type = body.get("type", "")
    table_name = body.get("table", "")
    record = body.get("record") or {}
    old_record = body.get("old_record") or {}

    # Persist raw event
    event = WebhookEvent.objects.create(
        source="rentengine",
        event_type=event_type,
        table_name=table_name,
        record=record,
        old_record=old_record,
    )
    logger.info(
        "RentEngine webhook received: %s %s (event %s)",
        event_type,
        table_name,
        event.pk,
    )

    # Process
    try:
        process_webhook_event(event)
    except Exception:
        logger.exception("Failed to process RentEngine webhook event %s", event.pk)
        return {"status": "error", "detail": "Processing failed"}

    return {"status": "ok"}
