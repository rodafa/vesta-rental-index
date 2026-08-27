"""
Webhook processing for RentEngine deliveries.
"""

import logging
from datetime import date, timedelta

from django.conf import settings
from django.db import IntegrityError

from automations.services import notify_slack
from core.models import Unit
from integrations.leadsimple.processes import create_process
from integrations.rentengine.client import RentEngineClient
from integrations.rentengine.mappers import map_leasing_event, map_prospect

from .models import (
    ApplicationProcessCreation,
    LeasingEvent,
    Prospect,
    RentEngineWebhookDelivery,
)

logger = logging.getLogger(__name__)


def process_webhook_delivery(delivery: RentEngineWebhookDelivery) -> None:
    """
    Process a single RentEngineWebhookDelivery row.

    Reads delivery.raw_payload, determines entity and operation, and upserts
    into Prospect or LeasingEvent via the existing mappers, or creates a
    LeadSimple process for rental_application_groups.

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

        raw_id = record.get("id")
        if raw_id is None:
            delivery.status = "unparseable"
            delivery.error_message = "Record has no 'id' field"
            delivery.save(
                update_fields=["status", "error_message", "target_entity", "operation"]
            )
            return

        # DELETE operations: never delete our rows. Append-only log.
        if delivery.operation.upper() == "DELETE":
            delivery.status = "ignored"
            delivery.error_message = (
                f"DELETE ignored (append-only log); rentengine_id={raw_id}"
            )
            delivery.save(
                update_fields=["status", "error_message", "target_entity", "operation"]
            )
            return

        # Route to entity handler
        if delivery.target_entity == "prospects":
            _upsert_prospect(delivery, record, raw_id)
        elif delivery.target_entity == "leasing_events":
            _upsert_leasing_event(delivery, record, raw_id)
        elif delivery.target_entity == "rental_application_groups":
            _handle_application_group(delivery, record, raw_id, data, payload)
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


def _upsert_prospect(delivery, record, raw_id):
    """Map and upsert a Prospect row."""
    try:
        rentengine_id = int(raw_id)
    except (ValueError, TypeError):
        delivery.status = "unparseable"
        delivery.error_message = f"Record 'id' is not an integer: {raw_id!r}"
        delivery.save(
            update_fields=["status", "error_message", "target_entity", "operation"]
        )
        return

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


def _upsert_leasing_event(delivery, record, raw_id):
    """Map and upsert a LeasingEvent row."""
    try:
        rentengine_id = int(raw_id)
    except (ValueError, TypeError):
        delivery.status = "unparseable"
        delivery.error_message = f"Record 'id' is not an integer: {raw_id!r}"
        delivery.save(
            update_fields=["status", "error_message", "target_entity", "operation"]
        )
        return

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


def _handle_application_group(delivery, record, raw_id, data, payload):
    """Handle a rental_application_groups webhook — create LeadSimple process."""
    group_id = str(raw_id)
    event_id = str(payload.get("event_id") or data.get("event_id") or "")

    # Gate: only fire on submitted_at transition from falsy to truthy
    old_submitted = (data.get("old_record") or {}).get("submitted_at")
    new_submitted = record.get("submitted_at")

    if old_submitted:
        delivery.status = "ignored"
        delivery.error_message = "already submitted previously"
        delivery.save(
            update_fields=["status", "error_message", "target_entity", "operation"]
        )
        logger.info(
            "application_group_already_submitted",
            extra={"group_id": group_id, "event_id": event_id},
        )
        return

    if not new_submitted:
        delivery.status = "ignored"
        delivery.error_message = "not yet submitted"
        delivery.save(
            update_fields=["status", "error_message", "target_entity", "operation"]
        )
        logger.info(
            "application_group_not_yet_submitted",
            extra={"group_id": group_id, "event_id": event_id},
        )
        return

    # Early dedupe check (saves a wasted enrichment call in the common case)
    existing = ApplicationProcessCreation.objects.filter(
        rentengine_group_id=group_id
    ).first()
    if existing:
        delivery.status = "ignored"
        delivery.error_message = (
            f"Process already created for group {group_id}: "
            f"leadsimple_process_id={existing.leadsimple_process_id}"
        )
        delivery.save(
            update_fields=["status", "error_message", "target_entity", "operation"]
        )
        logger.info(
            "application_group_duplicate",
            extra={
                "group_id": group_id,
                "event_id": event_id,
                "leadsimple_process_id": existing.leadsimple_process_id,
            },
        )
        return

    # Enrich from RentEngine /reporting/applications
    lead_applicant = ""
    address = ""

    created_at_raw = record.get("created_at")
    group_created = None
    if created_at_raw:
        try:
            group_created = date.fromisoformat(str(created_at_raw)[:10])
        except (ValueError, TypeError):
            pass

    if group_created is not None:
        try:
            client = RentEngineClient()
            rows = client.get_reporting_applications(
                start_date=group_created - timedelta(days=1),
                end_date=group_created + timedelta(days=1),
            )
            matched = False
            for row in rows:
                if str(row.get("application_group_id")) == group_id:
                    lead_applicant = row.get("lead_applicant") or ""
                    address = row.get("address") or ""
                    matched = True
                    break
            if not matched:
                logger.info(
                    "application_group_enrichment_no_match",
                    extra={"group_id": group_id, "event_id": event_id},
                )
        except Exception:
            logger.warning(
                "application_group_enrichment_failed",
                extra={"group_id": group_id, "event_id": event_id},
                exc_info=True,
            )
    else:
        logger.info(
            "application_enrichment_skipped_no_created_at",
            extra={"group_id": group_id, "event_id": event_id},
        )

    # Build process name
    applicant_name = lead_applicant or "Unknown Applicant"
    property_address = address or "Unknown Address"
    process_name = f"{applicant_name} <> {property_address}"

    # Reserve dedupe row (unique constraint is the actual race guarantee)
    try:
        reservation = ApplicationProcessCreation.objects.create(
            rentengine_group_id=group_id,
            rentengine_event_id=event_id,
            leadsimple_process_id="",
            process_name=process_name,
        )
    except IntegrityError:
        delivery.status = "ignored"
        delivery.error_message = (
            f"Process already reserved/created for group {group_id}"
        )
        delivery.save(
            update_fields=["status", "error_message", "target_entity", "operation"]
        )
        logger.info(
            "application_group_duplicate",
            extra={"group_id": group_id, "event_id": event_id},
        )
        return

    # Create LeadSimple process
    try:
        ls_process = create_process(
            name=process_name,
            process_type_id=settings.LEADSIMPLE_APPLICATION_PROCESS_TYPE_ID,
            stage_id=settings.LEADSIMPLE_APPLICATION_ENTRY_STAGE_ID,
        )
    except Exception as exc:
        reservation.delete()
        delivery.status = "unparseable"
        delivery.error_message = f"LeadSimple create failed: {exc}"
        delivery.save(
            update_fields=["status", "error_message", "target_entity", "operation"]
        )
        logger.error(
            "application_process_create_failed",
            extra={
                "group_id": group_id,
                "event_id": event_id,
                "process_name": process_name,
                "error": str(exc),
            },
        )
        try:
            notify_slack(
                f":rotating_light: *Application automation FAILED*\n"
                f"Group: {group_id}\n"
                f"Would have created: {process_name}\n"
                f"Error: {exc}"
            )
        except Exception:
            logger.exception("application_failure_slack_send_failed")
        return

    ls_process_id = str(ls_process.get("id") or "")

    # Finalize reservation with the actual LeadSimple process ID
    reservation.leadsimple_process_id = ls_process_id
    reservation.save(update_fields=["leadsimple_process_id"])

    # Notify Slack
    stage = ls_process.get("stage") or {}
    stage_name = stage.get("name") or "Unknown Stage"
    link = ls_process.get("link") or ls_process.get("url") or ""
    slack_text = (
        f"*New application process created*\n"
        f"Applicant: {applicant_name}\n"
        f"Property: {property_address}\n"
        f"Stage: {stage_name}\n"
        f"LeadSimple: {link or '(no link returned)'}"
    )
    try:
        notify_slack(slack_text)
    except Exception:
        logger.exception(
            "application_process_slack_send_failed",
            extra={
                "group_id": group_id,
                "leadsimple_process_id": ls_process_id,
            },
        )

    logger.info(
        "application_process_created",
        extra={
            "group_id": group_id,
            "event_id": event_id,
            "leadsimple_process_id": ls_process_id,
            "process_name": process_name,
        },
    )

    # Mark processed
    delivery.status = "processed"
    delivery.save(update_fields=["status", "target_entity", "operation"])
