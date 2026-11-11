"""
Database selectors for the utilities grid.

Each function makes one or two queries and returns plain dicts/lists
ready for the pure functions in grid.py.
"""

from collections import defaultdict

from django.db.models import Max, Q

from core.models import Lease, Unit
from utilities.models import PropertyUtility, UnitNote, UtilityProposal


def fetch_active_units():
    """
    Return a sorted list of active unit dicts with select_related property.
    Single query.
    """
    units = (
        Unit.objects
        .filter(is_active=True)
        .select_related("property")
        .order_by("property__address_line_1", "unit_number")
    )
    return [
        {
            "id": u.pk,
            "rentvine_id": u.rentvine_id,
            "display_address": u.display_address,
            "unit_number": u.unit_number or "",
        }
        for u in units
    ]


def fetch_approved_utilities():
    """
    Return {unit_id: {utility_type: {"responsibility": str, ...}}}.
    Single query over PropertyUtility.
    """
    result = defaultdict(dict)
    for pu in PropertyUtility.objects.all():
        result[pu.unit_id][pu.utility_type] = {
            "responsibility": pu.responsibility,
            "provider": pu.provider,
            "arrangement": pu.arrangement,
        }
    return dict(result)


def fetch_pending_proposals():
    """
    Return {rentvine_unit_id: {utility_type: [proposal_dicts]}}.
    Single query over UtilityProposal where status="pending".
    """
    result = defaultdict(lambda: defaultdict(list))
    for p in UtilityProposal.objects.filter(status="pending"):
        result[p.rentvine_unit_id][p.utility_type].append({
            "id": p.pk,
            "responsibility": p.responsibility,
            "has_conflict": p.has_conflict,
            "basis": p.basis,
            "provider": p.provider,
            "arrangement": p.arrangement,
            "evidence_quote": p.evidence_quote,
            "source_type": p.source_type,
            "source_level": p.source_level,
            "source_date": p.source_date,
            "quote_verified": p.quote_verified,
            "conflict_note": p.conflict_note,
        })
    return {k: dict(v) for k, v in result.items()}


def fetch_unit_notes():
    """
    Return {unit_id: {"latest": str, "count": int, "latest_date": datetime, "latest_author": str}}.
    Two queries: one for counts, one for latest per unit.
    """
    # Count per unit
    from django.db.models import Count
    counts = {
        row["unit_id"]: row["n"]
        for row in UnitNote.objects.values("unit_id").annotate(n=Count("id"))
    }

    if not counts:
        return {}

    # Latest note per unit (ordered scan, take first per unit)
    result = {}
    seen = set()
    for note in (
        UnitNote.objects
        .filter(unit_id__in=counts.keys())
        .select_related("created_by")
        .order_by("unit_id", "-created_at")
    ):
        if note.unit_id in seen:
            continue
        seen.add(note.unit_id)
        author = ""
        if note.created_by:
            author = note.created_by.get_full_name() or note.created_by.username
        result[note.unit_id] = {
            "latest": note.text,
            "count": counts[note.unit_id],
            "latest_date": note.created_at,
            "latest_author": author,
        }

    return result


def fetch_occupancy_map():
    """
    Return {unit_id: {"occupied": bool, "move_in_date": date|None}}.
    One annotated query: most recent active lease per unit.
    """
    active_leases = (
        Lease.objects
        .filter(primary_lease_status=2)
        .values("unit_id")
        .annotate(latest_move_in=Max("move_in_date"))
    )
    return {
        row["unit_id"]: {
            "occupied": True,
            "move_in_date": row["latest_move_in"],
        }
        for row in active_leases
    }


def fetch_all_provider_strings():
    """
    Return every non-empty provider string from pending proposals and
    approved utilities.  Duplicates included (frequency determines display name).
    """
    pending = list(
        UtilityProposal.objects
        .filter(status="pending")
        .exclude(provider="")
        .values_list("provider", flat=True)
    )
    approved = list(
        PropertyUtility.objects
        .exclude(provider="")
        .values_list("provider", flat=True)
    )
    return pending + approved


def fetch_panel_proposals(rentvine_unit_id, utility_type):
    """
    Fetch pending proposals for the side panel detail view.
    Includes alias types (well for water, septic for sewer).
    """
    if not rentvine_unit_id:
        return []

    from utilities.grid import ALIAS_MAP
    types = [utility_type]
    alias = ALIAS_MAP.get(utility_type)
    if alias:
        types.append(alias)
    types.append("other")

    return [
        {
            "id": p.pk,
            "utility_type": p.utility_type,
            "responsibility": p.responsibility,
            "has_conflict": p.has_conflict,
            "basis": p.basis,
            "provider": p.provider,
            "arrangement": p.arrangement,
            "evidence_quote": p.evidence_quote,
            "source_type": p.source_type,
            "source_level": p.source_level,
            "source_date": p.source_date,
            "quote_verified": p.quote_verified,
            "conflict_note": p.conflict_note,
        }
        for p in (
            UtilityProposal.objects
            .filter(
                rentvine_unit_id=rentvine_unit_id,
                utility_type__in=types,
                status="pending",
            )
            .order_by("-created_at")
        )
    ]


def fetch_panel_notes(unit_id, utility_type=""):
    """
    Fetch notes for the side panel.
    Shows notes for the specific utility type AND general notes (blank utility_type).
    """
    qs = UnitNote.objects.filter(unit_id=unit_id).select_related("created_by")
    if utility_type:
        qs = qs.filter(Q(utility_type=utility_type) | Q(utility_type=""))

    return [
        {
            "text": n.text,
            "utility_type": n.utility_type,
            "created_at": n.created_at,
            "author": (n.created_by.get_full_name() or n.created_by.username) if n.created_by else "",
        }
        for n in qs
    ]
