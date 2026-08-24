"""
Webhook processing for RentEngine deliveries.
"""

import logging

from core.models import Unit
from integrations.rentengine.mappers import map_leasing_event, map_prospect

from .models import LeasingEvent, Prospect, RentEngineWebhookDelivery

logger = logging.getLogger(__name__)


def process_webhook_delivery(delivery: RentEngineWebhookDelivery) -> None:
    """
    Process a single RentEngineWebhookDelivery row.

    Reads delivery.raw_payload, determines entity and operation, and upserts
    into Prospect or LeasingEvent via the existing mappers.

    Never raises — all failures are recorded on the delivery row.
    """
    try:
        payload = delivery.raw_payload
        if not isinstance(payload, dict):
            delivery.status = "unparseable"
            delivery.error_message = "Payload is not a JSON object"
            delivery.save(update_fields=["status", "error_message"])
            return

        # Hypothesis: payload contains data.table, data.type, data.record.
        # Fall back to top-level keys if no "data" wrapper.
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            delivery.status = "unparseable"
            delivery.error_message = "Payload 'data' is not an object"
            delivery.save(update_fields=["status", "error_message"])
            return

        delivery.target_entity = str(data.get("table") or "")
        delivery.operation = str(data.get("type") or "")
        record = data.get("record")

        if not record or not isinstance(record, dict):
            delivery.status = "unparseable"
            delivery.error_message = (
                f"No 'record' dict found in payload. "
                f"Payload top-level keys: {sorted(payload.keys())}. "
                f"Data keys: {sorted(data.keys())}."
            )
            delivery.save(
                update_fields=["status", "error_message", "target_entity", "operation"]
            )
            return

        rentengine_id = record.get("id")
        if rentengine_id is None:
            delivery.status = "unparseable"
            delivery.error_message = "Record has no 'id' field"
            delivery.save(
                update_fields=["status", "error_message", "target_entity", "operation"]
            )
            return

        try:
            rentengine_id = int(rentengine_id)
        except (ValueError, TypeError):
            delivery.status = "unparseable"
            delivery.error_message = (
                f"Record 'id' is not an integer: {rentengine_id!r}"
            )
            delivery.save(
                update_fields=["status", "error_message", "target_entity", "operation"]
            )
            return

        # DELETE operations: never delete our rows. Append-only log.
        if delivery.operation.upper() == "DELETE":
            delivery.status = "ignored"
            delivery.error_message = (
                f"DELETE ignored (append-only log); rentengine_id={rentengine_id}"
            )
            delivery.save(
                update_fields=["status", "error_message", "target_entity", "operation"]
            )
            return

        # Route to entity handler
        if delivery.target_entity == "prospects":
            _upsert_prospect(delivery, record, rentengine_id)
        elif delivery.target_entity == "leasing_events":
            _upsert_leasing_event(delivery, record, rentengine_id)
        else:
            delivery.status = "ignored"
            delivery.error_message = (
                f"Unknown target entity: {delivery.target_entity!r}. "
                f"Payload top-level keys: {sorted(payload.keys())}. "
                f"Data keys: {sorted(data.keys())}."
            )
            delivery.save(
                update_fields=["status", "error_message", "target_entity", "operation"]
            )

    except Exception as exc:
        delivery.status = "unparseable"
        delivery.error_message = f"{type(exc).__name__}: {exc}"
        delivery.save(
            update_fields=["status", "error_message", "target_entity", "operation"]
        )


def _resolve_unit(rentengine_unit_id):
    """Resolve a RentEngine unit ID to a core.Unit, or None."""
    if rentengine_unit_id is None:
        return None
    try:
        return Unit.objects.get(rentengine_id=rentengine_unit_id)
    except Unit.DoesNotExist:
        return None


def _upsert_prospect(delivery, record, rentengine_id):
    """Map and upsert a Prospect row."""
    mapped = map_prospect(record)
    unit_of_interest = mapped.pop("unit_of_interest", None)
    unit = _resolve_unit(unit_of_interest)

    prospect, _ = Prospect.objects.update_or_create(
        rentengine_id=rentengine_id,
        defaults={**mapped, "unit": unit},
    )

    delivery.status = "processed"
    delivery.resulting_prospect = prospect
    delivery.save(
        update_fields=[
            "status", "target_entity", "operation", "resulting_prospect",
        ]
    )


def _upsert_leasing_event(delivery, record, rentengine_id):
    """Map and upsert a LeasingEvent row."""
    mapped = map_leasing_event(record)
    unit_of_interest = mapped.pop("unit_of_interest", None)
    prospect_re_id = mapped.pop("prospect_id", None)
    unit = _resolve_unit(unit_of_interest)

    # Resolve prospect FK by RentEngine ID
    prospect = None
    if prospect_re_id is not None:
        try:
            prospect = Prospect.objects.get(rentengine_id=prospect_re_id)
        except Prospect.DoesNotExist:
            logger.warning(
                "leasing_event_prospect_missing",
                extra={
                    "prospect_rentengine_id": prospect_re_id,
                    "event_rentengine_id": rentengine_id,
                },
            )

    # Webhook payloads carry no unit_of_interest. When the event itself
    # has no unit, inherit from the prospect's unit_of_interest. The
    # event's own value still wins when present, because a person can
    # be a prospect on multiple units.
    if unit is None and prospect is not None:
        if prospect.unit_id is not None:
            unit = prospect.unit
        else:
            logger.info(
                "leasing_event_prospect_has_no_unit",
                extra={
                    "prospect_rentengine_id": prospect_re_id,
                    "event_rentengine_id": rentengine_id,
                },
            )

    # event_timestamp and event_date are required on LeasingEvent (non-nullable)
    if mapped.get("event_timestamp") is None:
        delivery.status = "unparseable"
        delivery.error_message = "Leasing event has no parseable event_timestamp"
        delivery.save(
            update_fields=["status", "error_message", "target_entity", "operation"]
        )
        return

    leasing_event, _ = LeasingEvent.objects.update_or_create(
        rentengine_id=rentengine_id,
        defaults={**mapped, "unit": unit, "prospect": prospect},
    )

    delivery.status = "processed"
    delivery.resulting_event = leasing_event
    delivery.save(
        update_fields=[
            "status", "target_entity", "operation", "resulting_event",
        ]
    )
