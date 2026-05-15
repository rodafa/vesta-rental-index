"""
Unit matching audit service layer.

Reconciles local Unit linkage against RentEngine inventory and optionally
posts a summary to Slack.
"""

import logging
from collections import defaultdict
from datetime import date

import requests
from django.conf import settings

from integrations.rentengine.client import RentEngineClient
from integrations.rentengine.mappers import map_re_unit
from properties.models import Unit
from properties.utils.address import normalize_address

logger = logging.getLogger(__name__)


def _format_re_address(defaults):
    """Build a single-line address string from mapper output."""
    parts = [
        defaults.get("address_line_1", ""),
        defaults.get("city", ""),
        defaults.get("state", ""),
        defaults.get("postal_code", ""),
    ]
    unit_num = defaults.get("unit_number", "")
    line1 = parts[0]
    if unit_num:
        line1 = f"{line1} Unit {unit_num}"
    return ", ".join(p for p in [line1, parts[1], parts[2], parts[3]] if p)


def _addresses_match(re_defaults, local_unit):
    """
    Compare addresses via normalized street address only.

    Local Units don't have city/state/postal populated (RentVine's unit
    endpoint doesn't return those fields — they live on Property). Comparing
    those would false-positive on every unit. The normalized street address
    is sufficient: units are already linked by rentengine_id, so we're only
    checking whether the street addresses agree.
    """
    # Build full RE address with unit number for normalization
    re_addr = re_defaults.get("address_line_1", "") or ""
    re_unit_num = (re_defaults.get("unit_number") or "").strip()
    if re_unit_num:
        re_addr = f"{re_addr} Unit {re_unit_num}"

    # Build full local address with unit designator for normalization.
    # address_line_2 is a bare designator ("2", "B") — prefix with "Unit"
    # so normalize_address() treats it the same as RE's "Unit 2".
    local_addr = local_unit.address_line_1 or ""
    local_line2 = (local_unit.address_line_2 or "").strip()
    if local_line2:
        local_addr = f"{local_addr} Unit {local_line2}"

    return normalize_address(re_addr) == normalize_address(local_addr)


def _suggest_candidate(defaults, all_local_units_by_postal):
    """
    Try to find a candidate local Unit for an unlinked RE unit.
    Matches on postal_code + street_number + street_name prefix.
    Returns (unit_id, unit_address) or (None, None).
    """
    postal = (defaults.get("postal_code") or "").strip()
    street_num = (defaults.get("street_number") or "").strip()
    if not postal or not street_num:
        return None, None

    candidates = all_local_units_by_postal.get(postal, [])
    matches = [
        u for u in candidates
        if (u.address_line_1 or "").lower().startswith(street_num.lower() + " ")
    ]

    if len(matches) == 1:
        return matches[0].id, matches[0].display_address

    # Try unit_number disambiguation
    unit_num = (defaults.get("unit_number") or "").strip().upper()
    if len(matches) > 1 and unit_num:
        for m in matches:
            local_unit_id = (m.address_line_2 or m.name or "").strip().upper()
            if local_unit_id == unit_num or local_unit_id.endswith(unit_num):
                return m.id, m.display_address

    return None, None


def run_audit():
    """
    Run the full unit matching audit against RentEngine inventory.

    Returns a dict with rentengine_unit_count, local_linked_count,
    anomalies (by class), and totals.
    """
    client = RentEngineClient()
    re_records = client.get_all("/units")

    # Parse each RE record through the same mapper the sync uses
    re_parsed = {}
    for record in re_records:
        try:
            re_id, defaults = map_re_unit(record)
            re_parsed[re_id] = (record, defaults)
        except Exception as exc:
            logger.warning("Failed to parse RE record: %s", exc)

    # Build local maps
    linked_units = Unit.objects.filter(
        rentengine_id__isnull=False
    ).select_related("property")
    local_by_re_id = {u.rentengine_id: u for u in linked_units}

    all_units = list(Unit.objects.select_related("property"))
    local_by_postal = defaultdict(list)
    for u in all_units:
        if u.postal_code:
            local_by_postal[u.postal_code.strip()].append(u)

    anomalies = {
        "stale_link": [],
        "unlinked_on_our_side": [],
        "address_drift": [],
        "multi_unit_ambiguity": [],
        "missing_rentengine_address": [],
    }

    # --- 1. Stale links ---
    re_id_set = set(re_parsed.keys())
    for re_id, unit in local_by_re_id.items():
        if re_id not in re_id_set:
            anomalies["stale_link"].append({
                "anomaly_class": "stale_link",
                "rentengine_unit_id": re_id,
                "rentengine_address": "",
                "local_unit_id": unit.id,
                "local_unit_address": unit.display_address,
                "suggested_local_unit_id": "",
                "suggested_local_unit_address": "",
                "notes": (
                    f"Local unit {unit.id} points to RE id {re_id} "
                    f"which no longer exists in RentEngine inventory"
                ),
            })

    # --- 2. Unlinked on our side ---
    local_re_id_set = set(local_by_re_id.keys())
    for re_id, (record, defaults) in re_parsed.items():
        if re_id not in local_re_id_set:
            suggested_id, suggested_addr = _suggest_candidate(
                defaults, local_by_postal
            )
            anomalies["unlinked_on_our_side"].append({
                "anomaly_class": "unlinked_on_our_side",
                "rentengine_unit_id": re_id,
                "rentengine_address": _format_re_address(defaults),
                "local_unit_id": "",
                "local_unit_address": "",
                "suggested_local_unit_id": suggested_id or "",
                "suggested_local_unit_address": suggested_addr or "",
                "notes": (
                    "Candidate found" if suggested_id
                    else "No candidate match found"
                ),
            })

    # --- 3. Address drift ---
    for re_id, (record, defaults) in re_parsed.items():
        if re_id in local_by_re_id:
            unit = local_by_re_id[re_id]
            if not _addresses_match(defaults, unit):
                anomalies["address_drift"].append({
                    "anomaly_class": "address_drift",
                    "rentengine_unit_id": re_id,
                    "rentengine_address": _format_re_address(defaults),
                    "local_unit_id": unit.id,
                    "local_unit_address": unit.display_address,
                    "suggested_local_unit_id": "",
                    "suggested_local_unit_address": "",
                    "notes": (
                        "Address fields disagree between RentEngine and local DB"
                    ),
                })

    # --- 4. Multi-unit ambiguity ---
    re_by_address_key = defaultdict(list)
    for re_id, (record, defaults) in re_parsed.items():
        postal = (defaults.get("postal_code") or "").strip()
        street_num = (defaults.get("street_number") or "").strip()
        addr = (defaults.get("address_line_1") or "").strip().lower()
        if postal and street_num:
            re_by_address_key[(postal, street_num, addr)].append(
                (re_id, defaults)
            )

    for key, group in re_by_address_key.items():
        if len(group) < 2:
            continue
        units_without_unit_num = [
            (re_id, d) for re_id, d in group
            if not (d.get("unit_number") or "").strip()
        ]
        if units_without_unit_num:
            re_ids = [str(re_id) for re_id, _ in group]
            for re_id, defaults in units_without_unit_num:
                anomalies["multi_unit_ambiguity"].append({
                    "anomaly_class": "multi_unit_ambiguity",
                    "rentengine_unit_id": re_id,
                    "rentengine_address": _format_re_address(defaults),
                    "local_unit_id": (
                        local_by_re_id[re_id].id
                        if re_id in local_by_re_id else ""
                    ),
                    "local_unit_address": (
                        local_by_re_id[re_id].display_address
                        if re_id in local_by_re_id else ""
                    ),
                    "suggested_local_unit_id": "",
                    "suggested_local_unit_address": "",
                    "notes": (
                        f"{len(group)} RE units share this address; "
                        f"this one has no unit_number. "
                        f"All RE IDs at address: {', '.join(re_ids)}"
                    ),
                })

    # --- 5. Missing RentEngine address ---
    for re_id, (record, defaults) in re_parsed.items():
        addr = (defaults.get("address_line_1") or "").strip()
        postal = (defaults.get("postal_code") or "").strip()
        city = (defaults.get("city") or "").strip()
        if not addr and not postal and not city:
            anomalies["missing_rentengine_address"].append({
                "anomaly_class": "missing_rentengine_address",
                "rentengine_unit_id": re_id,
                "rentengine_address": "",
                "local_unit_id": (
                    local_by_re_id[re_id].id
                    if re_id in local_by_re_id else ""
                ),
                "local_unit_address": (
                    local_by_re_id[re_id].display_address
                    if re_id in local_by_re_id else ""
                ),
                "suggested_local_unit_id": "",
                "suggested_local_unit_address": "",
                "notes": (
                    "RentEngine unit has no usable address fields "
                    "(all null/empty)"
                ),
            })

    totals = {k: len(v) for k, v in anomalies.items()}

    return {
        "rentengine_unit_count": len(re_parsed),
        "local_linked_count": len(local_by_re_id),
        "anomalies": anomalies,
        "totals": totals,
    }


def _format_sample_sections(audit_result):
    """Build sample anomaly sections for Slack output."""
    anomalies = audit_result["anomalies"]
    lines = []

    MAX_SAMPLES = 5

    # Stale links
    stale = anomalies.get("stale_link", [])
    if stale:
        show = stale[:MAX_SAMPLES]
        lines.append("")
        lines.append(
            f"*Sample stale links (showing {len(show)} of {len(stale)}):*"
        )
        for a in show:
            lines.append(
                f"\u2022 Local Unit {a['local_unit_id']} "
                f"({a['local_unit_address']}) "
                f"\u2192 was linked to RE unit {a['rentengine_unit_id']} "
                f"(no longer exists)"
            )

    # Unlinked on our side
    unlinked = anomalies.get("unlinked_on_our_side", [])
    if unlinked:
        show = unlinked[:MAX_SAMPLES]
        lines.append("")
        lines.append(
            f"*Sample unlinked on our side "
            f"(showing {len(show)} of {len(unlinked)}):*"
        )
        for a in show:
            if a["suggested_local_unit_id"]:
                lines.append(
                    f"\u2022 RE unit {a['rentengine_unit_id']} "
                    f"({a['rentengine_address']}) "
                    f"\u2192 suggested local Unit "
                    f"{a['suggested_local_unit_id']} "
                    f"({a['suggested_local_unit_address']}) "
                    f"\u2014 postal+street match"
                )
            else:
                lines.append(
                    f"\u2022 RE unit {a['rentengine_unit_id']} "
                    f"({a['rentengine_address']}) "
                    f"\u2192 no candidate found"
                )

    # Address drift
    drift = anomalies.get("address_drift", [])
    if drift:
        show = drift[:MAX_SAMPLES]
        lines.append("")
        lines.append(
            f"*Sample address drift "
            f"(showing {len(show)} of {len(drift)}):*"
        )
        for a in show:
            lines.append(
                f"\u2022 Local Unit {a['local_unit_id']} "
                f"({a['local_unit_address']}): "
                f"\"{a['local_unit_address']}\" "
                f"\u2194 RE: \"{a['rentengine_address']}\""
            )

    # Multi-unit ambiguity
    multi = anomalies.get("multi_unit_ambiguity", [])
    if multi:
        show = multi[:MAX_SAMPLES]
        lines.append("")
        lines.append(
            f"*Sample multi-unit ambiguity "
            f"(showing {len(show)} of {len(multi)}):*"
        )
        for a in show:
            lines.append(f"\u2022 {a['notes']}")

    return lines


def post_audit_summary(audit_result, include_samples=False):
    """Post a formatted audit summary to Slack."""
    webhook_url = settings.SLACK_LEASING_WEBHOOK_URL
    if not webhook_url:
        logger.warning("SLACK_LEASING_WEBHOOK_URL not configured. Skipping.")
        return

    totals = audit_result["totals"]
    total_anomalies = sum(totals.values())
    re_count = audit_result["rentengine_unit_count"]
    linked_count = audit_result["local_linked_count"]

    today = date.today()
    date_str = f"{today.strftime('%A')}, {today.strftime('%B')} {today.day}, {today.year}"

    if total_anomalies == 0:
        text = (
            f"\U0001f50d Unit Matching Audit \u2014 {date_str}\n"
            f"\u2705 Unit matching audit clean \u2014 "
            f"0 anomalies across {re_count} units"
        )
    else:
        lines = [
            f"\U0001f50d Unit Matching Audit \u2014 {date_str}",
            f"*Inventory:* {re_count} RE units | {linked_count} local linked",
            "",
            "*Anomalies:*",
        ]

        emoji_map = {
            "stale_link": "\U0001f517",
            "unlinked_on_our_side": "\u2753",
            "address_drift": "\U0001f4cd",
            "multi_unit_ambiguity": "\U0001f3d8\ufe0f",
            "missing_rentengine_address": "\U0001f4ed",
        }
        label_map = {
            "stale_link": "Stale link",
            "unlinked_on_our_side": "Unlinked on our side",
            "address_drift": "Address drift",
            "multi_unit_ambiguity": "Multi-unit ambiguity",
            "missing_rentengine_address": "Missing RE address",
        }

        for key in emoji_map:
            count = totals.get(key, 0)
            if count > 0:
                lines.append(
                    f"{emoji_map[key]} {label_map[key]}: {count}"
                )

        # Top unlinked candidates
        unlinked = audit_result["anomalies"].get("unlinked_on_our_side", [])
        with_suggestions = [
            a for a in unlinked if a["suggested_local_unit_id"]
        ]
        if with_suggestions:
            show = with_suggestions[:5]
            lines.append("")
            header = "*Top unlinked candidates"
            if len(with_suggestions) > 5:
                header += f" (showing first 5 of {len(with_suggestions)})"
            header += ":*"
            lines.append(header)
            for a in show:
                lines.append(
                    f"\u2022 RE {a['rentengine_unit_id']}: "
                    f"{a['rentengine_address']} "
                    f"\u2192 Local {a['suggested_local_unit_id']}: "
                    f"{a['suggested_local_unit_address']}"
                )

        if include_samples:
            lines.extend(_format_sample_sections(audit_result))

        text = "\n".join(lines)

    requests.post(webhook_url, json={"text": text}, timeout=15)
