"""
Database service functions for proposal review actions.

Thin orchestrators: fetch ORM objects, call pure decision functions from
grid.py, and write inside transaction.atomic.  No business logic here.
"""

import logging

from django.db import transaction
from django.utils import timezone

from core.models import Unit
from utilities.grid import (
    approved_rows_to_replace,
    blanket_columns_to_fill,
    check_approve_proposal,
    decide_mark_na,
    is_blanket_proposal,
    pending_ids_in_group,
    siblings_to_supersede,
    validate_reject,
)
from utilities.models import PropertyUtility, UtilityProposal

logger = logging.getLogger(__name__)


class ReviewError(Exception):
    """Raised when a review action cannot proceed."""


def approve_proposal(proposal_id, user):
    """
    Approve a pending proposal.  The server decides everything from the
    proposal itself — blanket vs normal, unit mapping, supersede set.

    Returns a success message string.
    Raises ReviewError on any problem.
    """
    with transaction.atomic():
        try:
            proposal = (
                UtilityProposal.objects
                .select_for_update()
                .get(pk=proposal_id)
            )
        except UtilityProposal.DoesNotExist:
            raise ReviewError("Proposal not found.")

        # Pure decision: can it be approved?
        ok, err = check_approve_proposal({
            "status": proposal.status,
            "responsibility": proposal.responsibility,
        })
        if not ok:
            raise ReviewError(err)

        # Is this a blanket proposal?
        proposal_dict = {
            "utility_type": proposal.utility_type,
            "responsibility": proposal.responsibility,
            "evidence_quote": proposal.evidence_quote,
        }
        if proposal.utility_type == "other":
            if not is_blanket_proposal(proposal_dict):
                raise ReviewError(
                    "This 'other' proposal is not a blanket statement and cannot be approved. "
                    "Use Change to set an answer for specific columns."
                )
            return _approve_blanket(proposal, user)

        return _approve_normal(proposal, user)


def _approve_normal(proposal, user):
    """Approve a single-column proposal inside an existing transaction."""
    now = timezone.now()

    # Map rentvine_unit_id to core.Unit
    unit = Unit.objects.filter(rentvine_id=proposal.rentvine_unit_id, is_active=True).first()
    if not unit:
        raise ReviewError(
            f"No active unit found for rentvine_unit_id {proposal.rentvine_unit_id}."
        )

    utype = proposal.utility_type

    # Remove other-type approved rows in the same column group
    approved_types = list(
        PropertyUtility.objects
        .filter(unit=unit)
        .values_list("utility_type", flat=True)
    )
    to_replace = approved_rows_to_replace(approved_types, utype)
    if to_replace:
        replaced = PropertyUtility.objects.filter(unit=unit, utility_type__in=to_replace).delete()
        logger.info(
            "approved_row_replaced",
            extra={
                "user_id": user.pk,
                "unit_id": unit.pk,
                "replaced_types": to_replace,
                "count": replaced[0],
            },
        )

    # Upsert PropertyUtility
    PropertyUtility.objects.update_or_create(
        unit=unit,
        utility_type=utype,
        defaults={
            "responsibility": proposal.responsibility,
            "provider": proposal.provider,
            "arrangement": proposal.arrangement,
            "extras": proposal.extras,
            "approved_from": proposal,
            "approved_by": user,
            "approved_at": now,
        },
    )

    # Mark proposal approved
    proposal.status = "approved"
    proposal.reviewed_by = user
    proposal.reviewed_at = now
    proposal.save(update_fields=["status", "reviewed_by", "reviewed_at"])

    # Supersede other pending proposals in the same column group
    pending = list(
        UtilityProposal.objects
        .filter(
            rentvine_unit_id=proposal.rentvine_unit_id,
            status="pending",
        )
        .values("id", "utility_type")
    )
    chosen = {"id": proposal.pk, "utility_type": utype}
    supersede_ids = siblings_to_supersede(chosen, pending)
    if supersede_ids:
        UtilityProposal.objects.filter(pk__in=supersede_ids).update(
            status="superseded",
            reviewed_by=user,
            reviewed_at=now,
            review_note=f"Superseded by approval of proposal {proposal.pk}",
        )

    logger.info(
        "proposal_approved",
        extra={
            "user_id": user.pk,
            "rentvine_unit_id": proposal.rentvine_unit_id,
            "utility_type": utype,
            "proposal_id": proposal.pk,
            "superseded_count": len(supersede_ids),
        },
    )

    return f"Approved {utype} for this unit."


def _approve_blanket(proposal, user):
    """Approve a blanket proposal inside an existing transaction."""
    now = timezone.now()

    unit = Unit.objects.filter(rentvine_id=proposal.rentvine_unit_id, is_active=True).first()
    if not unit:
        raise ReviewError(
            f"No active unit found for rentvine_unit_id {proposal.rentvine_unit_id}."
        )

    # Gather the unit's current approved types and pending proposals
    approved_types_map = {}
    for pu in PropertyUtility.objects.filter(unit=unit):
        approved_types_map[pu.utility_type] = pu

    pending_by_type = {}
    for p in UtilityProposal.objects.filter(
        rentvine_unit_id=proposal.rentvine_unit_id,
        status="pending",
    ).exclude(pk=proposal.pk):
        pending_by_type.setdefault(p.utility_type, []).append({
            "id": p.pk,
            "responsibility": p.responsibility,
        })

    # Pure decision: which columns to fill
    cols = blanket_columns_to_fill(
        {t: {} for t in approved_types_map},
        pending_by_type,
        proposal.responsibility,
    )

    # Create PropertyUtility rows for chosen columns
    for col in cols:
        PropertyUtility.objects.update_or_create(
            unit=unit,
            utility_type=col,
            defaults={
                "responsibility": proposal.responsibility,
                "provider": "",
                "arrangement": "",
                "extras": {},
                "approved_from": proposal,
                "approved_by": user,
                "approved_at": now,
            },
        )

    # Mark blanket proposal approved
    proposal.status = "approved"
    proposal.reviewed_by = user
    proposal.reviewed_at = now
    proposal.save(update_fields=["status", "reviewed_by", "reviewed_at"])

    logger.info(
        "blanket_proposal_approved",
        extra={
            "user_id": user.pk,
            "rentvine_unit_id": proposal.rentvine_unit_id,
            "proposal_id": proposal.pk,
            "columns_filled": cols,
        },
    )

    if cols:
        col_names = ", ".join(cols)
        return f"Blanket approved. Filled columns: {col_names}."
    return "Blanket approved. No columns needed filling (all already have data)."


def reject_proposal(proposal_id, user, review_note=""):
    """
    Reject a pending proposal.

    Re-extraction will not recreate it because its fingerprint still exists
    in the database; get_or_create on fingerprint will find the existing row.

    Returns a success message string.
    Raises ReviewError on any problem.
    """
    note = (review_note or "").strip()
    ok, err = validate_reject(note)
    if not ok:
        raise ReviewError(err)

    with transaction.atomic():
        try:
            proposal = (
                UtilityProposal.objects
                .select_for_update()
                .get(pk=proposal_id)
            )
        except UtilityProposal.DoesNotExist:
            raise ReviewError("Proposal not found.")

        if proposal.status != "pending":
            raise ReviewError("Proposal is no longer pending.")

        now = timezone.now()
        proposal.status = "rejected"
        proposal.reviewed_by = user
        proposal.reviewed_at = now
        proposal.review_note = note
        proposal.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note"])

    logger.info(
        "proposal_rejected",
        extra={
            "user_id": user.pk,
            "rentvine_unit_id": proposal.rentvine_unit_id,
            "utility_type": proposal.utility_type,
            "proposal_id": proposal.pk,
        },
    )

    return "Proposal rejected."


def _write_approved_and_supersede(
    unit, utility_type, responsibility, provider, extras, user, now,
    *, review_note="Superseded by manual answer",
):
    """
    Shared writer: replace/upsert an approved row and supersede pending
    proposals in the same column group.

    Must be called inside an existing transaction.atomic block.

    Steps:
    1. Delete approved rows in the same column group that have a different type.
    2. Upsert PropertyUtility for the given type.
    3. Supersede all pending proposals in the column group.
    """
    # 1. Replace other types in the same group
    approved_types = list(
        PropertyUtility.objects
        .filter(unit=unit)
        .values_list("utility_type", flat=True)
    )
    to_replace = approved_rows_to_replace(approved_types, utility_type)
    if to_replace:
        PropertyUtility.objects.filter(
            unit=unit, utility_type__in=to_replace,
        ).delete()

    # 2. Upsert
    PropertyUtility.objects.update_or_create(
        unit=unit,
        utility_type=utility_type,
        defaults={
            "responsibility": responsibility,
            "provider": provider,
            "arrangement": "",
            "extras": extras,
            "approved_from": None,
            "approved_by": user,
            "approved_at": now,
        },
    )

    # 3. Supersede pending proposals in the column group
    if unit.rentvine_id:
        pending = list(
            UtilityProposal.objects
            .filter(
                rentvine_unit_id=unit.rentvine_id,
                status="pending",
            )
            .values("id", "utility_type")
        )
        supersede_ids = pending_ids_in_group(utility_type, pending)
        if supersede_ids:
            UtilityProposal.objects.filter(pk__in=supersede_ids).update(
                status="superseded",
                reviewed_by=user,
                reviewed_at=now,
                review_note=review_note,
            )


def save_manual_answer(unit_id, cleaned_data, user):
    """
    Save a manual answer (Change) for a cell.

    Args:
        unit_id: the grid unit pk (core.Unit.pk).
        cleaned_data: dict from validate_manual_answer with utility_type,
                      responsibility, provider, extras.
        user: the request user.

    Returns a success message string.
    Raises ReviewError on any problem.
    """
    unit = Unit.objects.filter(pk=unit_id, is_active=True).first()
    if not unit:
        raise ReviewError("Unit not found or inactive.")

    utype = cleaned_data["utility_type"]
    now = timezone.now()

    with transaction.atomic():
        _write_approved_and_supersede(
            unit, utype,
            cleaned_data["responsibility"],
            cleaned_data["provider"],
            cleaned_data["extras"],
            user, now,
        )

    logger.info(
        "manual_answer_saved",
        extra={
            "user_id": user.pk,
            "unit_id": unit.pk,
            "utility_type": utype,
            "responsibility": cleaned_data["responsibility"],
        },
    )

    return f"Answer saved for {utype}."


def mark_na(unit_id, columns, user):
    """
    Mark one or more columns as N/A (not applicable) for a unit.

    Pure decision via decide_mark_na, then DB writes for columns that pass.
    Runs inside a single transaction with select_for_update on the Unit.

    Args:
        unit_id: the grid unit pk (core.Unit.pk).
        columns: list of column names (e.g. ["oil", "propane"]).
        user: the request user.

    Returns {column: (action, message)} where action is
    "written", "skipped", or "blocked".
    Raises ReviewError if the unit is not found.
    """
    now = timezone.now()

    with transaction.atomic():
        unit = (
            Unit.objects
            .select_for_update()
            .filter(pk=unit_id, is_active=True)
            .first()
        )
        if not unit:
            raise ReviewError("Unit not found or inactive.")

        # Gather data for the pure decision
        approved_types = list(
            PropertyUtility.objects
            .filter(unit=unit)
            .values_list("utility_type", flat=True)
        )
        pending = []
        if unit.rentvine_id:
            pending = list(
                UtilityProposal.objects
                .filter(
                    rentvine_unit_id=unit.rentvine_id,
                    status="pending",
                )
                .values("id", "utility_type", "responsibility")
            )

        # Pure decision
        results = decide_mark_na(columns, approved_types, pending)

        # Write for each "written" column
        for col, (action, _msg) in results.items():
            if action != "written":
                continue
            _write_approved_and_supersede(
                unit, col,
                "not_applicable", "", {},
                user, now,
                review_note="Superseded by N/A mark",
            )
            logger.info(
                "mark_na",
                extra={
                    "user_id": user.pk,
                    "unit_id": unit.pk,
                    "utility_type": col,
                },
            )

    return results
