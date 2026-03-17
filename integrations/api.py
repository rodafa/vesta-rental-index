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
    Validates X-API-Key header against the configured secret.
    RentEngine sends the API key as X-API-Key in webhook requests.

    If RENTENGINE.WEBHOOK_SECRET is not set (empty string), all requests
    are allowed — this supports local dev without configuring secrets.
    """

    param_name = "X-API-Key"

    def authenticate(self, request: HttpRequest, key: str | None):
        expected = settings.RENTENGINE.get("WEBHOOK_SECRET", "")
        if not expected:
            return "dev"  # No secret configured — allow all (dev mode)
        if key == expected:
            return key
        return None


class BoomPayWebhookAuth(APIKeyHeader):
    """
    Validates X-BoomPay-Signature header against the configured secret.

    If BOOMPAY.WEBHOOK_SECRET is not set (empty string), all requests
    are allowed — this supports local dev without configuring secrets.
    """

    param_name = "X-BoomPay-Signature"

    def authenticate(self, request: HttpRequest, key: str | None):
        expected = settings.BOOMPAY.get("WEBHOOK_SECRET", "")
        if not expected:
            return "dev"  # No secret configured — allow all (dev mode)
        if key == expected:
            return key
        return None


# ---------------------------------------------------------------------------
# RentEngine Endpoint
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


# ---------------------------------------------------------------------------
# SendGrid Delivery Webhook
# ---------------------------------------------------------------------------


@router.post("/sendgrid/", response={200: dict})
def sendgrid_webhook(request: HttpRequest):
    """
    Receive delivery event webhooks from SendGrid.

    SendGrid POSTs a JSON array of events. Each event includes:
        {
            "event": "delivered | open | bounce | ...",
            "sg_message_id": "...",
            "timestamp": 1234567890,
            "reason": "..." (bounce only)
        }

    We match on sendgrid_message_id to update OwnerReportNote status.

    Signature verification uses X-Twilio-Email-Event-Webhook-Signature header
    and SENDGRID_WEBHOOK_SECRET from settings. If the secret is not configured,
    verification is skipped (dev mode).
    """
    import hashlib
    import hmac
    import json

    from django.utils import timezone as tz

    from dashboard.models import OwnerReportNote

    # ── Signature verification ────────────────────────────────────────────
    secret = settings.SENDGRID_WEBHOOK_SECRET
    if secret:
        sig_header = request.META.get("HTTP_X_TWILIO_EMAIL_EVENT_WEBHOOK_SIGNATURE", "")
        ts_header = request.META.get("HTTP_X_TWILIO_EMAIL_EVENT_WEBHOOK_TIMESTAMP", "")
        if sig_header and ts_header:
            payload = ts_header.encode() + request.body
            expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, sig_header):
                logger.warning("SendGrid webhook: invalid signature")
                return {"status": "error", "detail": "Invalid signature"}
        else:
            logger.warning("SendGrid webhook: missing signature headers")
            return {"status": "error", "detail": "Missing signature headers"}

    # ── Parse body ────────────────────────────────────────────────────────
    try:
        events = json.loads(request.body)
        if not isinstance(events, list):
            events = [events]
    except (json.JSONDecodeError, ValueError):
        logger.warning("SendGrid webhook: invalid JSON body")
        return {"status": "error", "detail": "Invalid JSON"}

    processed = 0
    for event in events:
        event_type = event.get("event", "")
        # SendGrid appends filter info after a dot — use only the base message ID
        sg_msg_id = (event.get("sg_message_id") or "").split(".")[0]
        if not sg_msg_id:
            continue

        try:
            note = OwnerReportNote.objects.get(sendgrid_message_id=sg_msg_id)
        except OwnerReportNote.DoesNotExist:
            logger.debug("SendGrid webhook: no note for message_id %s", sg_msg_id)
            continue
        except OwnerReportNote.MultipleObjectsReturned:
            logger.warning("SendGrid webhook: multiple notes for message_id %s", sg_msg_id)
            continue

        if event_type == "delivered":
            note.status = "delivered"
            note.delivered_at = tz.now()
            note.save(update_fields=["status", "delivered_at"])
            logger.info("SendGrid: delivered note %s (owner %s)", note.pk, note.owner_id)

        elif event_type == "open":
            if not note.opened_at:
                note.opened_at = tz.now()
                note.save(update_fields=["opened_at"])
            logger.info("SendGrid: opened note %s (owner %s)", note.pk, note.owner_id)

        elif event_type == "bounce":
            note.status = "bounced"
            note.bounce_reason = event.get("reason") or ""
            note.save(update_fields=["status", "bounce_reason"])
            logger.warning(
                "SendGrid: bounce for note %s (owner %s): %s",
                note.pk, note.owner_id, note.bounce_reason,
            )

        processed += 1

    logger.info("SendGrid webhook: processed %d of %d events", processed, len(events))
    return {"status": "ok", "processed": processed}


# ---------------------------------------------------------------------------
# BoomPay Endpoint
# ---------------------------------------------------------------------------


@router.post("/boompay/", auth=[BoomPayWebhookAuth()], response={200: dict})
def boompay_webhook(request: HttpRequest):
    """
    Receive a webhook event from BoomPay/BoomScreen.

    Expected JSON body:
        {
            "event": "application_submitted | application_approved | ...",
            "data": { ... } or "application": { ... }
        }
    """
    from integrations.boompay.processors import process_boompay_webhook

    body = request.ninja_parsed_body if hasattr(request, "ninja_parsed_body") else {}
    if not body:
        import json

        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            logger.warning("BoomPay webhook: invalid JSON body")
            return {"status": "error", "detail": "Invalid JSON"}

    event_type = body.get("event", "")

    # Persist raw event
    event = WebhookEvent.objects.create(
        source="boompay",
        event_type=event_type,
        table_name="",  # BoomPay doesn't use table names
        record=body,
        old_record={},
    )
    logger.info(
        "BoomPay webhook received: %s (event %s)",
        event_type,
        event.pk,
    )

    # Process
    try:
        process_boompay_webhook(event)
    except Exception:
        logger.exception("Failed to process BoomPay webhook event %s", event.pk)
        return {"status": "error", "detail": "Processing failed"}

    return {"status": "ok"}
