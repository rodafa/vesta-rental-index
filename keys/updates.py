"""Apply logic for KeyBot update commands.

Plain-argument service functions (do_*) contain all business logic.
Thin apply_* wrappers unpack PendingKeyChange and delegate — both
Slack confirms and the dashboard call the same do_* functions.

Each do_* function expects to run inside transaction.atomic.
Returns None on success, or an error message string on refusal.

Never logs code text or note text — only ids and actions.
"""

import logging
import zoneinfo

from django.utils import timezone

from keys.models import AccessNote, Checkout, KeyChange, PendingKeyChange, Tag

_ET = zoneinfo.ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plain-argument service functions (shared by Slack + dashboard)
# ---------------------------------------------------------------------------


def do_set_code(
    *, unit_id, property_id, code, source, actor_name, source_ref,
):
    """Create an AccessNote for a lockbox code.

    Unit-level when unit_id is set; property-level otherwise.
    Returns None on success, or an error message string on refusal.
    """
    today = timezone.now().astimezone(_ET).date()

    if unit_id:
        note = AccessNote.objects.create(
            unit_id=unit_id,
            source=source,
            source_ref=source_ref,
            text=code,
            source_date=today,
        )
    else:
        note = AccessNote.objects.create(
            property_id=property_id,
            source=source,
            source_ref=source_ref,
            text=code,
            source_date=today,
        )

    KeyChange.objects.create(
        unit_id=unit_id,
        field="access_note",
        new_value=str(note.pk),
        changed_by=actor_name,
        reason="set_code",
        source_ref=source_ref,
    )

    logger.info(
        f"set_code unit={unit_id} property={property_id}"
        f" note={note.pk} by={actor_name} ref={source_ref}"
    )
    return None


def do_assign(*, tag_id, unit_id, actor_name, source_ref):
    """Assign a tag to a unit. Refuses retired tags.

    Returns None on success, or an error message string on refusal.
    """
    tag = Tag.objects.select_for_update().get(pk=tag_id)

    if tag.status == "retired":
        return f"{tag.key_id} is retired and cannot be assigned."

    old_unit_id = tag.unit_id

    tag.unit_id = unit_id
    tag.property = None
    tag.save(update_fields=["unit", "property", "updated_at"])

    KeyChange.objects.create(
        tag=tag,
        unit_id=unit_id,
        field="unit",
        old_value=str(old_unit_id or ""),
        new_value=str(unit_id),
        changed_by=actor_name,
        reason="assign",
        source_ref=source_ref,
    )

    logger.info(
        f"assign tag={tag.key_id} unit={unit_id}"
        f" by={actor_name} ref={source_ref}"
    )
    return None


def do_retire(*, tag_id, actor_name, source_ref):
    """Retire a tag. Refuses checked-out or already-retired tags.

    Returns None on success, or an error message string on refusal.
    """
    tag = Tag.objects.select_for_update().get(pk=tag_id)

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
        changed_by=actor_name,
        reason="retire",
        source_ref=source_ref,
    )

    logger.info(
        f"retire tag={tag.key_id} by={actor_name} ref={source_ref}"
    )
    return None


def do_mark_missing(*, tag_id, actor_name, source_ref):
    """Mark a tag as missing. Closes open checkout if any.

    Returns None on success, or an error message string on refusal.
    """
    tag = Tag.objects.select_for_update().get(pk=tag_id)
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
        changed_by=actor_name,
        reason=reason,
        source_ref=source_ref,
    )

    logger.info(
        f"mark_missing tag={tag.key_id} had_checkout={open_co is not None}"
        f" by={actor_name} ref={source_ref}"
    )
    return None


def do_found(*, tag_id, actor_name, source_ref):
    """Mark a missing tag as found (on_hook).

    Returns None on success, or an error message string on refusal.
    """
    tag = Tag.objects.select_for_update().get(pk=tag_id)

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
        changed_by=actor_name,
        reason="found",
        source_ref=source_ref,
    )

    logger.info(
        f"found tag={tag.key_id} by={actor_name} ref={source_ref}"
    )
    return None


def do_create_tag(*, box, number, unit_id=None, property_id=None, actor_name, source_ref):
    """Create a new Tag. Refuses if key_id already exists.

    Returns None on success, or an error message string on refusal.
    """
    key_id = f"{box.upper()}-{number:03d}"
    if Tag.objects.filter(key_id=key_id).exists():
        return f"{key_id} already exists."

    tag = Tag(box=box.upper(), number=number)
    if unit_id:
        tag.unit_id = unit_id
    elif property_id:
        tag.property_id = property_id
    tag.save()

    KeyChange.objects.create(
        tag=tag,
        unit_id=unit_id,
        field="created",
        new_value=key_id,
        changed_by=actor_name,
        reason="create_tag",
        source_ref=source_ref,
    )

    logger.info(
        f"create_tag tag={key_id} by={actor_name} ref={source_ref}"
    )
    return None


def do_hide_note(*, note_id, actor_name, source_ref):
    """Hide an AccessNote. Returns None on success, error string on refusal."""
    note = AccessNote.objects.get(pk=note_id)
    if note.hidden:
        return "Note is already hidden."

    note.hidden = True
    note.save(update_fields=["hidden"])

    KeyChange.objects.create(
        unit_id=note.unit_id,
        field="access_note_hidden",
        old_value=str(note.pk),
        new_value="hidden",
        changed_by=actor_name,
        reason="hide_note",
        source_ref=source_ref,
    )

    logger.info(
        f"hide_note note={note.pk} by={actor_name} ref={source_ref}"
    )
    return None


def do_edit_ring_notes(*, tag_id, ring_notes, actor_name, source_ref):
    """Update ring_notes on a tag.

    Returns None on success, or an error message string on refusal.
    """
    tag = Tag.objects.select_for_update().get(pk=tag_id)
    old_value = tag.ring_notes

    tag.ring_notes = ring_notes
    tag.save(update_fields=["ring_notes", "updated_at"])

    KeyChange.objects.create(
        tag=tag,
        unit=tag.unit,
        field="ring_notes",
        old_value=old_value,
        new_value=ring_notes,
        changed_by=actor_name,
        reason="edit_ring_notes",
        source_ref=source_ref,
    )

    logger.info(
        f"edit_ring_notes tag={tag.key_id} by={actor_name} ref={source_ref}"
    )
    return None


# ---------------------------------------------------------------------------
# Thin wrappers for Slack confirm path (unpack PendingKeyChange → do_*)
# ---------------------------------------------------------------------------


def apply_set_code(pending: PendingKeyChange) -> str | None:
    """Unpack a confirmed set_code PendingKeyChange and delegate."""
    payload = pending.payload
    return do_set_code(
        unit_id=payload.get("unit_id"),
        property_id=payload["property_id"],
        code=payload["code"],
        source="keybot",
        actor_name=pending.requester_slack_id,
        source_ref=str(pending.pk),
    )


def apply_assign(pending: PendingKeyChange) -> str | None:
    """Unpack a confirmed assign PendingKeyChange and delegate."""
    payload = pending.payload
    return do_assign(
        tag_id=payload["tag_id"],
        unit_id=payload["unit_id"],
        actor_name=pending.requester_slack_id,
        source_ref=str(pending.pk),
    )


def apply_retire(pending: PendingKeyChange) -> str | None:
    """Unpack a confirmed retire PendingKeyChange and delegate."""
    payload = pending.payload
    return do_retire(
        tag_id=payload["tag_id"],
        actor_name=pending.requester_slack_id,
        source_ref=str(pending.pk),
    )


def apply_mark_missing(pending: PendingKeyChange) -> str | None:
    """Unpack a confirmed mark_missing PendingKeyChange and delegate."""
    payload = pending.payload
    return do_mark_missing(
        tag_id=payload["tag_id"],
        actor_name=pending.requester_slack_id,
        source_ref=str(pending.pk),
    )


def apply_found(pending: PendingKeyChange) -> str | None:
    """Unpack a confirmed found PendingKeyChange and delegate."""
    payload = pending.payload
    return do_found(
        tag_id=payload["tag_id"],
        actor_name=pending.requester_slack_id,
        source_ref=str(pending.pk),
    )
