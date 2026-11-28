"""Key Health dashboard views.

Thin orchestrators — query logic in selectors, write logic in updates.
Never logs code text or note text.
"""

import json
import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.utils import timezone
from django.db.models import F, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from core.models import Unit
from keys.models import (
    AccessNote, Checkout, KeyHealthSnapshot, PendingKeyChange, Tag,
)
from keys.selectors import find_units, key_health
from keys.services import normalize_address, normalize_unit_token, unit_label
from keys.updates import (
    do_assign, do_create_tag, do_edit_ring_notes, do_found,
    do_hide_note, do_mark_missing, do_retire, do_set_code,
)

logger = logging.getLogger(__name__)

_PAGE_SIZE = 100


def _unit_sort_key(u):
    """Sort key: (street base name, house number int, unit token)."""
    addr = u.address_line_1 or u.property.address_line_1 or ""
    num_str, base, _ = normalize_address(addr)
    try:
        num = int(num_str)
    except (ValueError, TypeError):
        num = 0
    tok = normalize_unit_token(u.unit_number)
    try:
        tok_num = int(tok)
        return (base, num, 0, tok_num, "")
    except (ValueError, TypeError):
        return (base, num, 1, 0, tok)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deny(request):
    if not request.user.can_access("keys"):
        return HttpResponseForbidden("Access denied.")
    return None


def _back(request):
    qs = request.POST.get("_qs", "")
    url = "/dashboard/keys/"
    return redirect(f"{url}?{qs}" if qs else url)


def _ref(user):
    return f"dashboard:{user.pk}"


def _actor(user):
    return user.get_full_name() or user.username


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------

@login_required
def key_dashboard(request):
    deny = _deny(request)
    if deny:
        return deny

    health = key_health()

    # Scorecard counts
    scorecard = {
        "vacant_no_key_no_code": len(health["vacant_no_key_no_code"]),
        "vacant_no_key": len(health["vacant_no_key"]),
        "vacant_no_code": len(health["vacant_no_code"]),
        "overdue_checkouts": len(health["overdue_checkouts"]),
        "missing_tags": len(health["missing_tags"]),
        "duplicate_tags": len(health["duplicate_tags"]),
        "building_tags": len(health["building_tags"]),
        "orphan_tags": len(health["orphan_tags"]),
        "occupied_no_key": len(health["occupied_no_key"]),
        "occupied_no_code": len(health["occupied_no_code"]),
        "total_units": health["total_units"],
        "with_key": health["with_key_count"],
        "with_code": health["with_code_count"],
        "with_both": health["with_both_count"],
    }

    # Chart data from snapshots
    snapshots = list(
        KeyHealthSnapshot.objects.order_by("date").values("date", "counts")[:90]
    )
    chart_data = json.dumps([
        {
            "date": s["date"].isoformat(),
            "total": s["counts"].get("total_units", 0),
            "with_key": s["counts"].get("with_key", 0),
            "with_code": s["counts"].get("with_code", 0),
            "with_both": s["counts"].get("with_both", 0),
        }
        for s in snapshots
    ])

    # Unit table
    show_hidden = request.GET.get("show_hidden") == "1"
    rows = _build_rows(show_hidden=show_hidden)

    # Filters
    f = request.GET.get("filter", "")
    q = request.GET.get("q", "").strip()

    # Tile filters — sets built from health data for exact matching
    _tile_unit_ids = {
        "vacant_no_key_no_code": {i["unit_id"] for i in health["vacant_no_key_no_code"]},
        "vacant_no_key": {i["unit_id"] for i in health["vacant_no_key"]},
        "vacant_no_code": {i["unit_id"] for i in health["vacant_no_code"]},
        "occupied_no_key": {i["unit_id"] for i in health["occupied_no_key"]},
        "occupied_no_code": {i["unit_id"] for i in health["occupied_no_code"]},
        "duplicates": {d["unit_id"] for d in health["duplicate_tags"]},
    }
    _tile_prop_ids = {
        "building_tags": {d["property_id"] for d in health["building_tags"]},
    }

    if f in _tile_unit_ids:
        ids = _tile_unit_ids[f]
        rows = [r for r in rows if r["unit_id"] in ids]
    elif f in _tile_prop_ids:
        ids = _tile_prop_ids[f]
        rows = [r for r in rows if r["property_id"] in ids]
    elif f == "overdue":
        rows = [r for r in rows if "Overdue checkout" in r["flags"]]
    elif f == "missing":
        rows = [r for r in rows if "Missing tag" in r["flags"]]
    elif f == "with_key":
        rows = [r for r in rows if r["has_key"]]
    elif f == "with_code":
        rows = [r for r in rows if r["has_code"]]
    elif f == "with_both":
        rows = [r for r in rows if r["has_key"] and r["has_code"]]
    elif f == "without_key":
        rows = [r for r in rows if not r["has_key"]]
    elif f == "without_code":
        rows = [r for r in rows if not r["has_code"]]
    elif f == "without_both":
        rows = [r for r in rows if not (r["has_key"] and r["has_code"])]
    elif f == "problems":
        rows = [r for r in rows if r["flags"]]
    elif f == "vacant":
        rows = [r for r in rows if r["vacancy"] == "Available"]
    elif f == "occupied":
        rows = [r for r in rows if r["vacancy"] == "Leased"]
    elif f == "unknown":
        rows = [r for r in rows if not r["vacancy"]]

    if q:
        ql = q.lower()
        rows = [
            r for r in rows
            if ql in r["address"].lower()
            or any(
                ql in t["key_id"].lower()
                for t in r["tags"] + r["property_tags"]
            )
        ]

    # Label for active tile-filter chip (empty for general filters)
    _TILE_LABELS = {
        "vacant_no_key_no_code": "Vacant: No Key + No Code",
        "vacant_no_key": "Vacant: No Key",
        "vacant_no_code": "Vacant: No Code",
        "overdue": "Overdue Checkouts",
        "missing": "Missing Tags",
        "duplicates": "Duplicate Tags",
        "building_tags": "Building Tags",
        "with_key": "With Key",
        "with_code": "With Code",
        "with_both": "With Both",
        "without_key": "Without Key",
        "without_code": "Without Code",
        "without_both": "Without Both",
        "occupied_no_key": "Occupied: No Key",
        "occupied_no_code": "Occupied: No Code",
    }
    tile_filter_label = _TILE_LABELS.get(f, "")

    paginator = Paginator(rows, _PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page", "1"))

    return render(request, "keys/dashboard.html", {
        "scorecard": scorecard,
        "chart_data": chart_data,
        "page_obj": page_obj,
        "orphans": health["orphan_tags"],
        "filter_param": f,
        "search_param": q,
        "show_hidden": show_hidden,
        "tile_filter_label": tile_filter_label,
        "qs": request.GET.urlencode(),
    })


# ---------------------------------------------------------------------------
# Table builder (4 queries, no N+1)
# ---------------------------------------------------------------------------

def _build_rows(*, show_hidden=False):
    units = list(
        Unit.objects.filter(is_active=True, rentvine_id__isnull=False)
        .select_related("property")
    )
    units.sort(key=_unit_sort_key)
    if not units:
        return []

    unit_ids = [u.pk for u in units]
    prop_ids = list({u.property_id for u in units})

    # --- Tags (non-retired) ---
    tags_by_unit = {}
    tags_by_prop = {}
    tag_pks = []
    for t in (
        Tag.objects.filter(
            Q(unit_id__in=unit_ids)
            | Q(property_id__in=prop_ids, unit__isnull=True)
        )
        .exclude(status="retired")
        .order_by("box", "number")
    ):
        tag_pks.append(t.pk)
        if t.unit_id:
            tags_by_unit.setdefault(t.unit_id, []).append(t)
        elif t.property_id:
            tags_by_prop.setdefault(t.property_id, []).append(t)

    # --- Open checkouts ---
    co_map = {}
    if tag_pks:
        for co in Checkout.objects.filter(
            tag_id__in=tag_pks, returned_at__isnull=True,
        ):
            co_map[co.tag_id] = co

    # --- Access notes ---
    notes_qs = AccessNote.objects.filter(
        Q(unit_id__in=unit_ids)
        | Q(property_id__in=prop_ids, unit__isnull=True)
    )
    if not show_hidden:
        notes_qs = notes_qs.exclude(hidden=True)
    notes_qs = notes_qs.order_by(
        F("source_date").desc(nulls_last=True), "-created_at",
    )

    notes_by_unit = {}
    notes_by_prop = {}
    keybot_refs = []
    dashboard_user_ids = set()
    for n in notes_qs:
        if n.unit_id:
            notes_by_unit.setdefault(n.unit_id, []).append(n)
        elif n.property_id:
            notes_by_prop.setdefault(n.property_id, []).append(n)
        if n.source == "keybot" and n.source_ref:
            keybot_refs.append(n.source_ref)
        elif n.source == "dashboard" and n.source_ref.startswith("dashboard:"):
            dashboard_user_ids.add(n.source_ref.split(":", 1)[1])

    # Author lookup: keybot → PendingKeyChange.requester_name
    pending_names = {}
    if keybot_refs:
        for p in PendingKeyChange.objects.filter(
            pk__in=keybot_refs,
        ).only("pk", "requester_name"):
            pending_names[str(p.pk)] = p.requester_name

    # Author lookup: dashboard → User.get_full_name
    dashboard_names = {}
    if dashboard_user_ids:
        from accounts.models import User as AppUser
        for u in AppUser.objects.filter(
            pk__in=dashboard_user_ids,
        ).only("pk", "first_name", "last_name", "username"):
            dashboard_names[str(u.pk)] = u.get_full_name() or u.username

    _source_labels = {
        "rentvine_unit_note": "Rentvine",
        "rentvine_property_note": "Rentvine",
        "rentengine_internal_notes": "RentEngine",
        "rentengine_key_access": "RentEngine",
        "keybot": "KeyBot",
        "dashboard": "Dashboard",
    }

    def _tag_dict(tag):
        co = co_map.get(tag.pk)
        return {
            "pk": tag.pk,
            "key_id": tag.key_id,
            "status": tag.status,
            "status_display": tag.get_status_display(),
            "ring_notes": tag.ring_notes,
            "checkout": {
                "name": co.checked_out_by_name,
                "since": co.checked_out_at,
            } if co else None,
        }

    def _note_dict(note):
        author = ""
        if note.source == "keybot" and note.source_ref:
            author = pending_names.get(note.source_ref, "")
        elif note.source == "dashboard" and note.source_ref.startswith("dashboard:"):
            uid = note.source_ref.split(":", 1)[1]
            author = dashboard_names.get(uid, "")
        return {
            "pk": note.pk,
            "text": note.text,
            "source": _source_labels.get(note.source, note.source),
            "date": note.source_date,
            "author": author,
            "is_property_level": note.unit_id is None,
            "is_merged": False,
            "hidden": note.hidden,
        }

    now = timezone.now()
    overdue_threshold = now - timedelta(hours=24)

    rows = []
    for u in units:
        u_tags = tags_by_unit.get(u.pk, [])
        p_tags = tags_by_prop.get(u.property_id, [])
        u_notes = notes_by_unit.get(u.pk, [])
        p_notes = notes_by_prop.get(u.property_id, [])

        # Note dedup: identical text in unit + property → one "merged" entry
        u_texts = {n.text for n in u_notes}
        merged_texts = {n.text for n in p_notes} & u_texts
        deduped = []
        for n in u_notes:
            d = _note_dict(n)
            if n.text in merged_texts:
                d["is_merged"] = True
            deduped.append(d)
        for n in p_notes:
            if n.text not in merged_texts:
                deduped.append(_note_dict(n))
        all_notes = deduped[:5]

        has_key = bool(u_tags or p_tags)
        has_code = bool(u_notes or p_notes)
        vacancy = u.rentengine_status or ""

        # Flags — all vacancy states, matching key_health definitions
        flags = []
        if not has_key and not has_code:
            if vacancy == "Available":
                flags.append("No key AND no code")
            elif vacancy == "Leased":
                flags.append("Occupied, no key")
                flags.append("Occupied, no code")
            else:
                flags.append("No key")
                flags.append("No code")
        elif not has_key:
            if vacancy == "Leased":
                flags.append("Occupied, no key")
            else:
                flags.append("No key")
        elif not has_code:
            if vacancy == "Leased":
                flags.append("Occupied, no code")
            else:
                flags.append("No code")

        # Duplicate tags (unit-level and property-level)
        if len(u_tags) >= 2:
            flags.append("Duplicate tags")
        if len(p_tags) >= 2:
            flags.append("Building tags")

        # Missing tag
        all_tags = u_tags + p_tags
        if any(t.status == "missing" for t in all_tags):
            flags.append("Missing tag")

        # Overdue checkout (>24h)
        for t in all_tags:
            co = co_map.get(t.pk)
            if co and co.checked_out_at < overdue_threshold:
                flags.append("Overdue checkout")
                break

        rows.append({
            "unit_id": u.pk,
            "property_id": u.property_id,
            "address": unit_label(
                u.address_line_1, u.property.address_line_1, u.unit_number,
            ),
            "vacancy": vacancy,
            "vacancy_label": (
                "Vacant" if vacancy == "Available"
                else "Leased" if vacancy == "Leased"
                else "Unknown"
            ),
            "tags": [_tag_dict(t) for t in u_tags],
            "property_tags": [_tag_dict(t) for t in p_tags],
            "notes": all_notes,
            "flags": flags,
            "has_key": has_key,
            "has_code": has_code,
        })

    return rows


# ---------------------------------------------------------------------------
# POST actions — each writes through do_* then redirects with flash
# ---------------------------------------------------------------------------

@login_required
@require_POST
def action_set_code(request):
    deny = _deny(request)
    if deny:
        return deny

    unit_id = request.POST.get("unit_id") or None
    property_id = request.POST.get("property_id")
    code = request.POST.get("code", "").strip()

    if not code:
        messages.error(request, "Code cannot be empty.")
        return _back(request)
    if not property_id:
        messages.error(request, "Property is required.")
        return _back(request)

    with transaction.atomic():
        error = do_set_code(
            unit_id=int(unit_id) if unit_id else None,
            property_id=int(property_id),
            code=code,
            source="dashboard",
            actor_name=_actor(request.user),
            source_ref=_ref(request.user),
        )

    if error:
        messages.error(request, error)
    else:
        messages.success(request, "Code saved.")
    return _back(request)


@login_required
@require_POST
def action_assign(request):
    deny = _deny(request)
    if deny:
        return deny

    tag_id = request.POST.get("tag_id")
    key_id = request.POST.get("key_id", "").strip()
    unit_id = request.POST.get("unit_id")

    # Resolve key_id → tag_id
    if not tag_id and key_id:
        tag = Tag.objects.filter(key_id=key_id).first()
        if not tag:
            messages.error(request, f"No tag found with key ID '{key_id}'.")
            return _back(request)
        tag_id = tag.pk

    if not tag_id or not unit_id:
        messages.error(request, "Tag and unit are required.")
        return _back(request)

    with transaction.atomic():
        error = do_assign(
            tag_id=int(tag_id),
            unit_id=int(unit_id),
            actor_name=_actor(request.user),
            source_ref=_ref(request.user),
        )

    if error:
        messages.error(request, error)
    else:
        messages.success(request, "Tag assigned.")
    return _back(request)


@login_required
@require_POST
def action_retire(request):
    deny = _deny(request)
    if deny:
        return deny

    tag_id = request.POST.get("tag_id")
    if not tag_id:
        messages.error(request, "Tag is required.")
        return _back(request)

    with transaction.atomic():
        error = do_retire(
            tag_id=int(tag_id),
            actor_name=_actor(request.user),
            source_ref=_ref(request.user),
        )

    if error:
        messages.error(request, error)
    else:
        messages.success(request, "Tag retired.")
    return _back(request)


@login_required
@require_POST
def action_mark_missing(request):
    deny = _deny(request)
    if deny:
        return deny

    tag_id = request.POST.get("tag_id")
    if not tag_id:
        messages.error(request, "Tag is required.")
        return _back(request)

    with transaction.atomic():
        error = do_mark_missing(
            tag_id=int(tag_id),
            actor_name=_actor(request.user),
            source_ref=_ref(request.user),
        )

    if error:
        messages.error(request, error)
    else:
        messages.success(request, "Tag marked missing.")
    return _back(request)


@login_required
@require_POST
def action_found(request):
    deny = _deny(request)
    if deny:
        return deny

    tag_id = request.POST.get("tag_id")
    if not tag_id:
        messages.error(request, "Tag is required.")
        return _back(request)

    with transaction.atomic():
        error = do_found(
            tag_id=int(tag_id),
            actor_name=_actor(request.user),
            source_ref=_ref(request.user),
        )

    if error:
        messages.error(request, error)
    else:
        messages.success(request, "Tag marked found.")
    return _back(request)


@login_required
@require_POST
def action_create_tag(request):
    deny = _deny(request)
    if deny:
        return deny

    box = request.POST.get("box", "").strip().upper()
    number_str = request.POST.get("number", "").strip()
    unit_id = request.POST.get("unit_id") or None
    property_id = request.POST.get("property_id") or None
    code = request.POST.get("code", "").strip()
    ring_notes = request.POST.get("ring_notes", "").strip()
    whole_property = request.POST.get("whole_property") == "1"

    if box not in ("A", "B"):
        messages.error(request, "Box must be A or B.")
        return _back(request)
    try:
        number = int(number_str)
    except (ValueError, TypeError):
        messages.error(request, "Number must be an integer.")
        return _back(request)
    if number < 1 or number > 200:
        messages.error(request, "Number must be 1\u2013200.")
        return _back(request)

    if code and not property_id:
        messages.error(request, "Select a unit before adding a code.")
        return _back(request)

    key_id = f"{box}-{number:03d}"
    actor = _actor(request.user)
    ref = _ref(request.user)
    error = None

    with transaction.atomic():
        error = do_create_tag(
            box=box,
            number=number,
            unit_id=int(unit_id) if unit_id else None,
            property_id=int(property_id) if property_id else None,
            actor_name=actor,
            source_ref=ref,
        )

        if not error and ring_notes:
            tag = Tag.objects.get(key_id=key_id)
            error = do_edit_ring_notes(
                tag_id=tag.pk,
                ring_notes=ring_notes,
                actor_name=actor,
                source_ref=ref,
            )

        if not error and code:
            error = do_set_code(
                unit_id=None if whole_property else (int(unit_id) if unit_id else None),
                property_id=int(property_id),
                code=code,
                source="dashboard",
                actor_name=actor,
                source_ref=ref,
            )

        if error:
            transaction.set_rollback(True)

    if error:
        messages.error(request, error)
    else:
        extras = []
        if code:
            extras.append("code")
        if ring_notes:
            extras.append("ring notes")
        if unit_id:
            try:
                unit = Unit.objects.select_related("property").get(
                    pk=int(unit_id),
                )
                addr = unit_label(
                    unit.address_line_1, unit.property.address_line_1,
                    unit.unit_number,
                )
                msg = f"{key_id} added to {addr}"
            except Unit.DoesNotExist:
                msg = f"Tag {key_id} created"
        else:
            msg = f"Tag {key_id} created"
        if extras:
            msg += " with " + " and ".join(extras)
        msg += "."
        messages.success(request, msg)
    return _back(request)


@login_required
@require_POST
def action_hide_note(request):
    deny = _deny(request)
    if deny:
        return deny

    note_id = request.POST.get("note_id")
    if not note_id:
        messages.error(request, "Note is required.")
        return _back(request)

    with transaction.atomic():
        error = do_hide_note(
            note_id=int(note_id),
            actor_name=_actor(request.user),
            source_ref=_ref(request.user),
        )

    if error:
        messages.error(request, error)
    else:
        messages.success(request, "Note hidden.")
    return _back(request)


@login_required
@require_POST
def action_edit_ring_notes(request):
    deny = _deny(request)
    if deny:
        return deny

    tag_id = request.POST.get("tag_id")
    ring_notes = request.POST.get("ring_notes", "").strip()
    if not tag_id:
        messages.error(request, "Tag is required.")
        return _back(request)

    with transaction.atomic():
        error = do_edit_ring_notes(
            tag_id=int(tag_id),
            ring_notes=ring_notes,
            actor_name=_actor(request.user),
            source_ref=_ref(request.user),
        )

    if error:
        messages.error(request, error)
    else:
        messages.success(request, "Ring notes updated.")
    return _back(request)


# ---------------------------------------------------------------------------
# AJAX: unit search for orphan tag assignment
# ---------------------------------------------------------------------------

@login_required
@require_GET
def search_units(request):
    deny = _deny(request)
    if deny:
        return JsonResponse({"error": "Access denied"}, status=403)

    q = request.GET.get("q", "").strip()
    if len(q) < 2:
        return JsonResponse({"results": []})

    result = find_units(q)
    units = result["units"][:10]

    return JsonResponse({
        "results": [
            {
                "unit_id": u.pk,
                "property_id": u.property_id,
                "address": unit_label(
                    u.address_line_1, u.property.address_line_1,
                    u.unit_number,
                ),
            }
            for u in units
        ],
    })
