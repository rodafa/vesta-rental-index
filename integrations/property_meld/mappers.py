"""
Field mapping: Property Meld API JSON -> Meld model field dicts.

Field names confirmed from live API inspection on 2026-03-24.
"""

import logging

from integrations.rentvine.mappers import _safe_date, _safe_datetime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Status sets — used by services.py and the daily summary command.
# ---------------------------------------------------------------------------

OPEN_STATUSES = {
    "PENDING_ASSIGNMENT",
    "PENDING_VENDOR",
    "PENDING_ESTIMATES",
    "PENDING_MORE_MANAGEMENT_AVAILABILITY",
    "PENDING_MORE_VENDOR_AVAILABILITY",
    "PENDING_COMPLETION",
}

CLOSED_STATUSES = {
    "COMPLETED",
    "MANAGER_CANCELED",
    "TENANT_CANCELED",
    "MAINTENANCE_COULD_NOT_COMPLETE",
    "VENDOR_COULD_NOT_COMPLETE",
}

# Vendor accepted the work but hasn't scheduled yet
VENDOR_ACCEPTED_STATUSES = {"PENDING_COMPLETION"}

# Meld is waiting on owner approval
AWAITING_OWNER_APPROVAL_STATUS = "OWNER_APPROVAL_REQUESTED"

# ---------------------------------------------------------------------------
# Email status buckets — used for owner maintenance summary emails.
# Could-not-complete → Open bucket (per spec).
# ---------------------------------------------------------------------------

EMAIL_CLOSED_BUCKET = {"COMPLETED"}

EMAIL_CANCELED_BUCKET = {"MANAGER_CANCELED", "TENANT_CANCELED"}

# Everything else (including COULD_NOT_COMPLETE variants) → open
# Check: status NOT in EMAIL_CLOSED_BUCKET and NOT in EMAIL_CANCELED_BUCKET

# ---------------------------------------------------------------------------
# Owner approval normalisation
# ---------------------------------------------------------------------------

_OWNER_APPROVAL_MAP = {
    "OWNER_APPROVAL_NOT_REQUESTED": "Not Requested",
    "OWNER_APPROVAL_REQUESTED": "Requested",
    "OWNER_APPROVAL_APPROVED": "Approved",
    "OWNER_APPROVAL_NOT_APPROVED": "Not Approved",
}


def _normalise_owner_approval(raw):
    if not raw:
        return "Not Requested"
    return _OWNER_APPROVAL_MAP.get(str(raw).upper(), "Not Requested")


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _extract_coordinator_name(coordinator):
    """Pull first+last name from the nested coordinator dict."""
    if not coordinator or not isinstance(coordinator, dict):
        return ""
    user = coordinator.get("user") or {}
    if isinstance(user, dict):
        first = user.get("first_name") or ""
        last = user.get("last_name") or ""
        return f"{first} {last}".strip()
    return ""


def _extract_vendor_name(vendor_assignment_requests):
    """Return the name of the accepted vendor (first found), or empty string."""
    if not vendor_assignment_requests:
        return ""
    for req in vendor_assignment_requests:
        if not isinstance(req, dict):
            continue
        # Accepted requests have `accepted` set and `rejected`/`canceled` null
        if req.get("accepted") and not req.get("rejected") and not req.get("canceled"):
            vendor = req.get("vendor") or {}
            if isinstance(vendor, dict):
                return vendor.get("name") or ""
    # Fall back to the first vendor request if none are accepted
    first = vendor_assignment_requests[0]
    if isinstance(first, dict):
        vendor = first.get("vendor") or {}
        if isinstance(vendor, dict):
            return vendor.get("name") or ""
    return ""


def _extract_scheduled_date(vendorappointment, managementappointment):
    """
    Pull the earliest scheduled date from vendor or management appointments.
    vendorappointment[].availability_segment.event.dtstart
    """
    dates = []
    for appt_list in (vendorappointment or [], managementappointment or []):
        for appt in appt_list:
            if not isinstance(appt, dict):
                continue
            seg = appt.get("availability_segment") or {}
            event = seg.get("event") or {}
            dtstart = event.get("dtstart")
            if dtstart:
                d = _safe_date(dtstart)
                if d:
                    dates.append(d)
    return min(dates) if dates else None


# ---------------------------------------------------------------------------
# Main mapper
# ---------------------------------------------------------------------------

def map_meld(data):
    """
    Map a single Property Meld API record to (property_meld_id, defaults_dict).
    Raises ValueError if no usable ID found.
    """
    meld_id = data.get("id")
    if not meld_id:
        raise ValueError(f"No id in meld record: {list(data.keys())}")
    property_meld_id = str(meld_id)

    brief_description = data.get("brief_description") or ""
    category = data.get("work_category") or ""
    priority = data.get("priority") or ""
    status = data.get("status") or ""

    coordinator_name = _extract_coordinator_name(data.get("coordinator"))
    assigned_vendor_name = _extract_vendor_name(data.get("vendor_assignment_requests") or [])

    # Address: prefer prop_address, fall back to unit_address
    prop_addr = data.get("prop_address") or {}
    unit_addr = data.get("unit_address") or {}
    property_address = ""
    if isinstance(prop_addr, dict):
        property_address = prop_addr.get("full_address") or ""
    if not property_address and isinstance(unit_addr, dict):
        property_address = unit_addr.get("full_address") or ""

    # Property / unit refs (IDs as strings)
    prop_ref = data.get("prop")
    property_meld_property_id = str(prop_ref) if prop_ref is not None else ""
    unit_ref_val = data.get("unit")
    unit_ref = str(unit_ref_val) if unit_ref_val is not None else ""

    resident_presence_required = bool(data.get("tenant_presence_required") or False)

    scheduled_date = _extract_scheduled_date(
        data.get("vendorappointment"),
        data.get("managementappointment"),
    )

    # Date fields
    marked_complete = _safe_datetime(data.get("marked_complete"))
    completion_date = _safe_datetime(data.get("completion_date"))
    started = _safe_datetime(data.get("started"))
    due_date = _safe_date(data.get("due_date"))

    owner_approval_status = _normalise_owner_approval(data.get("owner_approval_status"))

    # has_invoice: not directly in the record; use work_entries as a proxy
    work_entries = data.get("work_entries") or []
    has_invoice = len(work_entries) > 0

    tags = []  # Property Meld doesn't surface tags in v2 list endpoint

    source_created_at = _safe_datetime(data.get("created"))
    source_modified_at = _safe_datetime(data.get("updated"))

    defaults = {
        "brief_description": str(brief_description),
        "category": str(category),
        "priority": str(priority),
        "status": str(status),
        "assigned_vendor_name": assigned_vendor_name,
        "coordinator_name": coordinator_name,
        "property_address": str(property_address),
        "property_meld_property_id": property_meld_property_id,
        "unit_ref": unit_ref,
        "resident_presence_required": resident_presence_required,
        "scheduled_date": scheduled_date,
        "marked_complete": marked_complete,
        "completion_date": completion_date,
        "started": started,
        "due_date": due_date,
        "owner_approval_status": owner_approval_status,
        "has_invoice": has_invoice,
        "tags": tags,
        # New fields from API
        "reference_id": data.get("reference_id") or "",
        "description": data.get("description") or "",
        "work_type": data.get("work_type") or "",
        "work_location": data.get("work_location") or "",
        "origin": data.get("origin") or "",
        "is_active": data.get("is_active", True),
        "maintenance_notes": data.get("maintenance_notes") or "",
        "completion_notes": data.get("completion_notes") or "",
        "reason_cannot_complete": data.get("reason_cannot_complete") or "",
        "resolution_type": data.get("resolution_type") or "",
        "parent_meld_id": str(data.get("parent") or ""),
        "recurring_meld_id": str(data.get("recurring_meld") or ""),
        "merged_meld_data": data.get("merged_meld") or {},
        "tenant_rating": data.get("tenant_rating"),
        "raw_data": data,
        "source_created_at": source_created_at,
        "source_modified_at": source_modified_at,
    }

    return property_meld_id, defaults


