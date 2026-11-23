"""Apply logic for KeyBot update commands.

Each function expects to be called inside transaction.atomic (the caller
locks PendingKeyChange with select_for_update first).  Returns None on
success, or an error message string on refusal.

Never logs code text or note text — only ids and actions.
"""

import logging
import zoneinfo

from django.utils import timezone

from keys.models import AccessNote, Checkout, KeyChange, PendingKeyChange, Tag

_ET = zoneinfo.ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)


def apply_set_code(pending: PendingKeyChange) -> str | None:
    """Create an AccessNote from a confirmed set_code command.

    Payload keys: unit_id (int | None), property_id (int), code (str).
    Unit-level when unit_id is set; property-level otherwise.
    """
    payload = pending.payload
    unit_id = payload.get("unit_id")
    property_id = payload["property_id"]
    code = payload["code"]
    ref = str(pending.pk)
    today = timezone.now().astimezone(_ET).date()

    if unit_id:
        note = AccessNote.objects.create(
            unit_id=unit_id,
            source="keybot",
            source_ref=ref,
            text=code,
            source_date=today,
        )
    else:
        note = AccessNote.objects.create(
            property_id=property_id,
            source="keybot",
            source_ref=ref,
            text=code,
            source_date=today,
        )

    KeyChange.objects.create(
        unit_id=unit_id,
        field="access_note",
        new_value=str(note.pk),
        changed_by=pending.requester_slack_id,
        reason="set_code",
        source_ref=ref,
    )

    logger.info(
        f"keybot_set_code pending={pending.pk}"
        f" unit={unit_id} property={property_id}"
        f" note={note.pk} by={pending.requester_slack_id}"
    )
    return None


def apply_assign(pending: PendingKeyChange) -> str | None:
    """Assign a tag to a unit from a confirmed assign command.

    Payload keys: tag_id (int), unit_id (int).
    Refuses retired tags.
    """
    payload = pending.payload
    tag = Tag.objects.select_for_update().get(pk=payload["tag_id"])
    ref = str(pending.pk)

    if tag.status == "retired":
        return f"{tag.key_id} is retired and cannot be assigned."

    old_unit_id = tag.unit_id
    new_unit_id = payload["unit_id"]

    tag.unit_id = new_unit_id
    tag.property = None
    tag.save(update_fields=["unit", "property", "updated_at"])

    KeyChange.objects.create(
        tag=tag,
        unit_id=new_unit_id,
        field="unit",
        old_value=str(old_unit_id or ""),
        new_value=str(new_unit_id),
        changed_by=pending.requester_slack_id,
        reason="assign",
        source_ref=ref,
    )

    logger.info(
        f"keybot_assign pending={pending.pk}"
        f" tag={tag.key_id} unit={new_unit_id}"
        f" by={pending.requester_slack_id}"
    )
    return None


def apply_retire(pending: PendingKeyChange) -> str | None:
    """Retire a tag from a confirmed retire command.

    Payload keys: tag_id (int).
    Refuses if the tag is currently checked out.
    """
    payload = pending.payload
    tag = Tag.objects.select_for_update().get(pk=payload["tag_id"])
    ref = str(pending.pk)

    if tag.status == "checked_out":
        return f"{tag.key_id} is currently checked out \u2014 return it first."
    if tag.status == "retired":
        return f"{tag.key_id} is already retired."

    old_status = tag.status
    tag.status = "retired"
    tag.save(update_fields=["status", "updated_at"])

    KeyChange.objects.create(
        tag=tag,
        unit=tag.unit,
        field="status",
        old_value=old_status,
        new_value="retired",
        changed_by=pending.requester_slack_id,
        reason="retire",
        source_ref=ref,
    )

    logger.info(
        f"keybot_retire pending={pending.pk}"
        f" tag={tag.key_id} by={pending.requester_slack_id}"
    )
    return None


def apply_mark_missing(pending: PendingKeyChange) -> str | None:
    """Mark a tag as missing from a confirmed mark_missing command.

    If the tag is checked out, closes the open checkout directly
    (returned_at = now) WITHOUT transitioning through on_hook first.
    Tag goes straight to status=missing.

    Payload keys: tag_id (int).
    """
    payload = pending.payload
    tag = Tag.objects.select_for_update().get(pk=payload["tag_id"])
    ref = str(pending.pk)
    now = timezone.now()

    if tag.status == "retired":
        return f"{tag.key_id} is retired."
    if tag.status == "missing":
        return f"{tag.key_id} is already marked missing."

    old_status = tag.status

    # If checked out, close the checkout directly (no on_hook transition)
    reason = "mark_missing"
    open_co = Checkout.objects.filter(
        tag=tag, returned_at__isnull=True,
    ).first()
    if open_co:
        open_co.returned_at = now
        open_co.save(update_fields=["returned_at"])
        reason = (
            f"mark_missing; closed open checkout"
            f" by {open_co.checked_out_by_name}"
        )

    tag.status = "missing"
    tag.save(update_fields=["status", "updated_at"])

    KeyChange.objects.create(
        tag=tag,
        unit=tag.unit,
        field="status",
        old_value=old_status,
        new_value="missing",
        changed_by=pending.requester_slack_id,
        reason=reason,
        source_ref=ref,
    )

    logger.info(
        f"keybot_mark_missing pending={pending.pk}"
        f" tag={tag.key_id} had_checkout={open_co is not None}"
        f" by={pending.requester_slack_id}"
    )
    return None


def apply_found(pending: PendingKeyChange) -> str | None:
    """Mark a missing tag as found (on_hook) from a confirmed found command.

    Payload keys: tag_id (int).
    Refuses if the tag is not currently status=missing.
    """
    payload = pending.payload
    tag = Tag.objects.select_for_update().get(pk=payload["tag_id"])
    ref = str(pending.pk)

    if tag.status != "missing":
        return (
            f"{tag.key_id} is not marked missing"
            f" (current: {tag.get_status_display().lower()})."
        )

    tag.status = "on_hook"
    tag.save(update_fields=["status", "updated_at"])

    KeyChange.objects.create(
        tag=tag,
        unit=tag.unit,
        field="status",
        old_value="missing",
        new_value="on_hook",
        changed_by=pending.requester_slack_id,
        reason="found",
        source_ref=ref,
    )

    logger.info(
        f"keybot_found pending={pending.pk}"
        f" tag={tag.key_id} by={pending.requester_slack_id}"
    )
    return None
