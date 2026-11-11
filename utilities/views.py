"""Dashboard views for the utilities grid."""

import csv
import logging
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods

from core.models import Unit
from utilities.grid import (
    ALIAS_MAP,
    GRID_COLUMNS,
    RESPONSIBILITY_DISPLAY,
    VALID_FILTERS,
    apply_filter,
    build_csv_rows,
    build_grid,
    build_provider_display_map,
    compute_tile_counts,
    prepare_panel_proposals,
)
from utilities.models import UTILITY_TYPE_CHOICES, UnitNote
from utilities.selectors import (
    fetch_active_units,
    fetch_all_provider_strings,
    fetch_approved_utilities,
    fetch_occupancy_map,
    fetch_panel_notes,
    fetch_panel_proposals,
    fetch_pending_proposals,
    fetch_unit_notes,
)

logger = logging.getLogger(__name__)

_VALID_UTILITY_COLUMNS = set(GRID_COLUMNS)
_VALID_UTILITY_TYPES = {c[0] for c in UTILITY_TYPE_CHOICES}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_full_grid():
    """Build the grid from DB data. Returns (all_rows, tile_counts, approved_map, provider_map)."""
    units = fetch_active_units()
    approved = fetch_approved_utilities()
    proposals = fetch_pending_proposals()
    notes = fetch_unit_notes()
    occupancy = fetch_occupancy_map()
    rows = build_grid(units, approved, proposals, notes, occupancy)
    tiles = compute_tile_counts(rows)
    provider_map = build_provider_display_map(fetch_all_provider_strings())
    return rows, tiles, approved, provider_map


def _grid_url(params):
    """Build a URL for the grid page with the given GET params."""
    base = "/dashboard/utilities/"
    clean = {k: v for k, v in params.items() if v}
    if clean:
        return f"{base}?{urlencode(clean)}"
    return base


def _preserve_get_params(request):
    """Collect current GET params that should survive a POST redirect."""
    params = {}
    for key in ("filter", "q", "unit", "utility"):
        val = request.GET.get(key, "")
        if val:
            params[key] = val
    return params


# ---------------------------------------------------------------------------
# Grid page (GET + POST)
# ---------------------------------------------------------------------------

@require_http_methods(["GET", "POST"])
@login_required
def utility_grid(request):
    """GET: render the grid. POST: add a note then redirect."""
    if not request.user.can_access("utilities"):
        return HttpResponseForbidden("Access denied.")

    if request.method == "POST":
        return _add_note(request)
    return _render_grid(request)


def _render_grid(request):
    all_rows, tiles, approved_map, provider_map = _build_full_grid()

    # Filters
    filter_name = request.GET.get("filter", "all")
    if filter_name not in VALID_FILTERS:
        filter_name = "all"
    query = request.GET.get("q", "").strip()

    filtered_rows = apply_filter(all_rows, filter_name, query)

    # Base query string for links (preserves filter + search)
    base_params = {}
    if filter_name != "all":
        base_params["filter"] = filter_name
    if query:
        base_params["q"] = query
    base_qs = urlencode(base_params) if base_params else ""

    # Side panel
    panel = None
    panel_unit_str = request.GET.get("unit", "")
    panel_utility = request.GET.get("utility", "")

    if panel_unit_str:
        try:
            panel_unit_id = int(panel_unit_str)
        except (ValueError, TypeError):
            panel_unit_id = None

        if panel_utility and panel_utility not in _VALID_UTILITY_COLUMNS:
            panel_utility = ""

        if panel_unit_id:
            # Find the row in the full grid (not filtered)
            unit_row = None
            for row in all_rows:
                if row["unit_id"] == panel_unit_id:
                    unit_row = row
                    break

            if unit_row:
                if panel_utility:
                    # Cell-level panel
                    cell = unit_row["cells"].get(panel_utility)
                    raw_proposals = fetch_panel_proposals(
                        unit_row["rentvine_id"], panel_utility,
                    )
                    proposals = prepare_panel_proposals(
                        raw_proposals, panel_utility,
                    )
                    notes = fetch_panel_notes(panel_unit_id, panel_utility)

                    approved_detail = None
                    unit_approved = approved_map.get(panel_unit_id, {})
                    if panel_utility in unit_approved:
                        approved_detail = unit_approved[panel_utility]
                    else:
                        alias = ALIAS_MAP.get(panel_utility)
                        if alias and alias in unit_approved:
                            approved_detail = unit_approved[alias]
                            approved_detail = {**approved_detail, "alias_type": alias}

                    if approved_detail:
                        raw_prov = approved_detail.get("provider") or ""
                        approved_detail = {
                            **approved_detail,
                            "responsibility_display": RESPONSIBILITY_DISPLAY.get(
                                approved_detail["responsibility"],
                                approved_detail["responsibility"],
                            ),
                            "provider": provider_map.get(raw_prov, raw_prov),
                        }

                    proposals_with_display = []
                    for p in proposals:
                        raw_prov = p.get("provider") or ""
                        proposals_with_display.append({
                            **p,
                            "responsibility_display": RESPONSIBILITY_DISPLAY.get(
                                p["responsibility"], p["responsibility"],
                            ),
                            "provider": provider_map.get(raw_prov, raw_prov),
                        })

                    panel = {
                        "unit_id": panel_unit_id,
                        "utility": panel_utility,
                        "utility_display": dict(UTILITY_TYPE_CHOICES).get(
                            panel_utility, panel_utility.capitalize()
                        ),
                        "address": unit_row["address"],
                        "cell": cell,
                        "proposals": proposals_with_display,
                        "notes": notes,
                        "approved": approved_detail,
                    }
                else:
                    # Unit-level panel (all notes)
                    notes = fetch_panel_notes(panel_unit_id)
                    panel = {
                        "unit_id": panel_unit_id,
                        "utility": "",
                        "utility_display": "Notes",
                        "address": unit_row["address"],
                        "cell": None,
                        "proposals": [],
                        "notes": notes,
                        "approved": None,
                        "utility_choices": UTILITY_TYPE_CHOICES,
                    }

    return render(request, "utilities/grid.html", {
        "rows": filtered_rows,
        "tiles": tiles,
        "filter": filter_name,
        "query": query,
        "columns": GRID_COLUMNS,
        "panel": panel,
        "total_units": len(all_rows),
        "base_qs": base_qs,
    })


# ---------------------------------------------------------------------------
# Add note (POST handler)
# ---------------------------------------------------------------------------

def _add_note(request):
    unit_id_str = request.POST.get("unit_id", "")
    utility_type = request.POST.get("utility_type", "").strip()
    text = request.POST.get("text", "").strip()

    errors = []

    # Validate unit
    unit = None
    try:
        unit_id = int(unit_id_str)
        unit = Unit.objects.filter(pk=unit_id, is_active=True).first()
        if not unit:
            errors.append("Unit not found or inactive.")
    except (ValueError, TypeError):
        errors.append("Invalid unit.")

    # Validate utility_type (blank is allowed)
    if utility_type and utility_type not in _VALID_UTILITY_TYPES:
        errors.append("Invalid utility type.")

    # Validate text
    if not text:
        errors.append("Note text is required.")
    elif len(text) > 2000:
        errors.append("Note text must be 2000 characters or fewer.")

    params = _preserve_get_params(request)

    if errors:
        messages.error(request, " ".join(errors))
        return HttpResponseRedirect(_grid_url(params))

    UnitNote.objects.create(
        unit=unit,
        utility_type=utility_type,
        text=text,
        created_by=request.user,
    )

    logger.info(
        "utility_note_created",
        extra={
            "unit_id": unit.pk,
            "utility_type": utility_type or "(general)",
            "user": request.user.username,
        },
    )

    messages.success(request, "Note added.")
    return HttpResponseRedirect(_grid_url(params))


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

@require_GET
@login_required
def utility_grid_csv(request):
    """Export the grid as CSV with the same filters applied."""
    if not request.user.can_access("utilities"):
        return HttpResponseForbidden("Access denied.")

    all_rows, _, _, _ = _build_full_grid()

    filter_name = request.GET.get("filter", "all")
    if filter_name not in VALID_FILTERS:
        filter_name = "all"
    query = request.GET.get("q", "").strip()

    rows = apply_filter(all_rows, filter_name, query)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="utilities_grid.csv"'

    writer = csv.writer(response)

    header = ["Address", "Unit", "Occupancy"]
    header += [col.capitalize() for col in GRID_COLUMNS]
    header += ["Latest Note"]
    writer.writerow(header)

    for line in build_csv_rows(rows):
        writer.writerow(line)

    return response
