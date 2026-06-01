"""
Webhook event processors for BoomPay/BoomScreen.

Dispatches WebhookEvent records to the appropriate handler based on
event_type, creating or updating ScreeningApplication / ScreeningReport records.
"""

import logging

from django.utils import timezone

from integrations.models import WebhookEvent
from screening.models import ScreeningApplication, ScreeningReport

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Status mapping: BoomPay event types → ScreeningApplication status
# ---------------------------------------------------------------------------

_APPLICATION_STATUS_MAP = {
    "application_submitted": "pending",
    "application_started": "pending",
    "applicant_submitted": "pending",
    "application_under_review": "in_progress",
    "application_updated": "in_progress",
    "application_approved": "completed",
    "application_conditionally_approved": "completed",
    "application_declined": "completed",
    "application_canceled": "expired",
}


def process_boompay_webhook(event: WebhookEvent):
    """
    Process a single BoomPay WebhookEvent and update domain models.

    Marks the event as processed on success, or stores the error on failure.
    """
    try:
        handler = _HANDLERS.get(event.event_type)
        if handler is None:
            logger.info(
                "No handler for BoomPay event type %r (event %s), marking processed",
                event.event_type,
                event.pk,
            )
        else:
            handler(event)

        event.processed = True
        event.processed_at = timezone.now()
        event.save(update_fields=["processed", "processed_at"])

    except Exception:
        logger.exception("Error processing BoomPay webhook event %s", event.pk)
        import traceback

        event.processing_error = traceback.format_exc()
        event.save(update_fields=["processing_error"])
        raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_data(event: WebhookEvent) -> dict:
    """Extract the data payload from a BoomPay webhook event."""
    record = event.record or {}
    # BoomPay nests data under "data" or "application"
    return record.get("data") or record.get("application") or record


def _extract_boompay_id(data: dict) -> str | None:
    """Pull boompay_id from data, trying common field names."""
    return str(data.get("id") or data.get("application_id") or "") or None


def _extract_applicant_name(data: dict) -> str:
    """Build applicant name from data fields."""
    name = data.get("applicant_name") or data.get("name") or ""
    if not name:
        first = data.get("first_name", "")
        last = data.get("last_name", "")
        name = f"{first} {last}".strip()
    return name


# ---------------------------------------------------------------------------
# Application event handlers
# ---------------------------------------------------------------------------


def _handle_application_event(event: WebhookEvent):
    """Handle any application-related event."""
    data = _extract_data(event)
    boompay_id = _extract_boompay_id(data)
    if not boompay_id:
        logger.warning(
            "BoomPay application event missing ID, skipping (event %s)", event.pk
        )
        return

    status = _APPLICATION_STATUS_MAP.get(event.event_type, "pending")
    defaults = {
        "applicant_name": _extract_applicant_name(data),
        "applicant_email": data.get("email") or data.get("applicant_email") or "",
        "status": status,
        "raw_data": data,
    }

    # Set timestamps based on status
    if status == "pending" and data.get("submitted_at"):
        defaults["submitted_at"] = data["submitted_at"]
    if status == "completed":
        defaults["completed_at"] = timezone.now()

    obj, created = ScreeningApplication.objects.update_or_create(
        boompay_id=boompay_id,
        defaults=defaults,
    )
    logger.info(
        "ScreeningApplication %s (boompay_id=%s) via webhook %s [%s]",
        "created" if created else "updated",
        boompay_id,
        event.pk,
        event.event_type,
    )


def _handle_identity_verification(event: WebhookEvent):
    """Handle identity_verification_finished — upsert ScreeningReport."""
    data = _extract_data(event)
    boompay_id = _extract_boompay_id(data)
    app_boompay_id = str(data.get("application_id") or "") or None

    if not boompay_id:
        logger.warning(
            "BoomPay identity_verification event missing ID, skipping (event %s)",
            event.pk,
        )
        return

    # Find parent ScreeningApplication
    screening_app = None
    if app_boompay_id:
        screening_app = ScreeningApplication.objects.filter(
            boompay_id=app_boompay_id
        ).first()

    if not screening_app:
        logger.warning(
            "No ScreeningApplication found for identity verification "
            "(app_boompay_id=%s, event %s), skipping",
            app_boompay_id,
            event.pk,
        )
        return

    report_id = f"identity-{boompay_id}"
    defaults = {
        "screening_application": screening_app,
        "report_type": "identity",
        "decision": "pass" if data.get("verified") else "review",
        "completed_at": timezone.now(),
        "report_data": data.get("report_data") or data.get("results") or {},
        "raw_data": data,
    }

    obj, created = ScreeningReport.objects.update_or_create(
        boompay_id=report_id,
        defaults=defaults,
    )
    logger.info(
        "ScreeningReport (identity) %s (boompay_id=%s) via webhook %s",
        "created" if created else "updated",
        report_id,
        event.pk,
    )


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------

_HANDLERS = {
    "application_submitted": _handle_application_event,
    "application_started": _handle_application_event,
    "applicant_submitted": _handle_application_event,
    "application_under_review": _handle_application_event,
    "application_updated": _handle_application_event,
    "application_approved": _handle_application_event,
    "application_conditionally_approved": _handle_application_event,
    "application_declined": _handle_application_event,
    "application_canceled": _handle_application_event,
    "identity_verification_finished": _handle_identity_verification,
}
