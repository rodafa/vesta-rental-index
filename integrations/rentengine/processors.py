"""
Webhook event processors for RentEngine.

Dispatches WebhookEvent records to the appropriate handler based on
table_name, creating or updating Prospect / LeasingEvent records.
"""

import logging

from django.utils import timezone

from integrations.models import WebhookEvent
from integrations.rentengine.mappers import (
    map_leasing_event_webhook,
    map_prospect_webhook,
)
from leasing.models import LeasingEvent, Prospect
from properties.models import Unit

logger = logging.getLogger(__name__)


def process_webhook_event(event: WebhookEvent):
    """
    Process a single WebhookEvent and update domain models accordingly.

    Marks the event as processed (with timestamp) on success, or stores
    the error message on failure.
    """
    try:
        handler = _HANDLERS.get(event.table_name)
        if handler is None:
            logger.warning(
                "No handler for table %r (event %s), skipping",
                event.table_name,
                event.pk,
            )
        else:
            handler(event)

        event.processed = True
        event.processed_at = timezone.now()
        event.save(update_fields=["processed", "processed_at"])

    except Exception:
        logger.exception("Error processing webhook event %s", event.pk)
        import traceback

        event.processing_error = traceback.format_exc()
        event.save(update_fields=["processing_error"])
        raise


# ---------------------------------------------------------------------------
# Per-table handlers
# ---------------------------------------------------------------------------


def _handle_prospect(event: WebhookEvent):
    record = event.record or {}
    rentengine_id = record.get("id")
    if not rentengine_id:
        logger.warning("Prospect webhook missing record.id, skipping (event %s)", event.pk)
        return

    if event.event_type.upper() == "DELETE":
        logger.info(
            "Prospect DELETE received (rentengine_id=%s) — preserving history, no action",
            rentengine_id,
        )
        return

    defaults = map_prospect_webhook(record)

    # Resolve unit FK
    unit_id = record.get("unit_id")
    if unit_id:
        defaults["unit_of_interest"] = Unit.objects.filter(
            rentengine_id=unit_id
        ).first()

    obj, created = Prospect.objects.update_or_create(
        rentengine_id=rentengine_id,
        defaults=defaults,
    )
    logger.info(
        "Prospect %s (rentengine_id=%s) via webhook %s",
        "created" if created else "updated",
        rentengine_id,
        event.pk,
    )


def _handle_leasing_event(event: WebhookEvent):
    record = event.record or {}
    rentengine_id = record.get("id")
    if not rentengine_id:
        logger.warning(
            "LeasingEvent webhook missing record.id, skipping (event %s)", event.pk
        )
        return

    defaults = map_leasing_event_webhook(record)

    # Resolve prospect FK
    prospect_id = record.get("prospect_id")
    if prospect_id:
        defaults["prospect"] = Prospect.objects.filter(
            rentengine_id=prospect_id
        ).first()

    # Resolve unit FK
    unit_id = record.get("unit_id")
    if unit_id:
        defaults["unit"] = Unit.objects.filter(rentengine_id=unit_id).first()

    obj, created = LeasingEvent.objects.update_or_create(
        rentengine_id=rentengine_id,
        defaults=defaults,
    )
    logger.info(
        "LeasingEvent %s (rentengine_id=%s) via webhook %s",
        "created" if created else "updated",
        rentengine_id,
        event.pk,
    )


_HANDLERS = {
    "prospects": _handle_prospect,
    "leasing_events": _handle_leasing_event,
}
