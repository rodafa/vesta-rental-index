"""
Mason work-order data fetch and transformation.

Batch-sweep strategy (called ONCE per generation run, not per portfolio):

Part 1 — unbounded, for Open/Scheduled:
    GET /work-orders for each open-ish status (created, reviewed, estimated,
    work_in_progress) with NO date lower bound.

Part 2 — windowed, for Completed/Cancelled:
    GET /work-orders?updatedAfter=<period_start minus 2 days buffer>.

Sub-resource sweeps (/visits, /work-order-activity-logs) cover both parts.
Vendor names resolved via batched GET /contacts.

Returns an index structure consumed by maintenance.mason_selectors.
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from maintenance.models import WorkOrder

from .client import MasonClient

logger = logging.getLogger(__name__)

def _date_to_utc_iso(d):
    """Convert a date object to a timezone-aware UTC ISO 8601 timestamp.

    The Mason API spec requires all date-filter values to include a timezone
    offset.  A bare date string silently matches zero records.
    """
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).isoformat()


# Mason statuses considered "open" — fetched without date lower bound.
_OPEN_STATUSES = ("created", "reviewed", "estimated", "work_in_progress")

# Mason statuses that map to Completed.
_COMPLETED_STATUSES = {"work_done", "closed"}

# Mason statuses that map to Cancelled.
_CANCELLED_STATUSES = {"cancelled"}

# Mason statuses to exclude entirely (logged, not rendered).
_EXCLUDED_STATUSES = {"duplicate"}

# Mason status that falls back to Rentvine derived_status.
_RENTVINE_FALLBACK_STATUSES = {"not_handled_by_mason"}


def _build_rentvine_lookup():
    """
    Build {rentvine_id: WorkOrder} lookup for all active local work orders.

    Used to join Mason WOs (via pms.workOrderId) to our local records for
    property → portfolio mapping and Rentvine-sourced fields.
    """
    return {
        wo.rentvine_id: wo
        for wo in WorkOrder.objects.filter(is_active=True).select_related(
            "unit", "property", "portfolio",
        )
    }


def _index_by_work_order_id(records, key="masonWorkOrderId"):
    """Index a list of sub-resource records by their work-order ID.

    Returns {work_order_id: [record, ...]} — a single WO may have multiple
    visits or activity-log entries.
    """
    index = {}
    for rec in records:
        wo_id = rec.get(key)
        if wo_id:
            index.setdefault(wo_id, []).append(rec)
    return index


def _resolve_vendor_names(work_orders, client):
    """
    Batch-resolve vendorContactId → display name via GET /contacts.

    Mutates each work-order dict in place, setting ``_vendor_name``.
    """
    vendor_ids = set()
    for wo in work_orders:
        vid = wo.get("vendorContactId")
        if vid:
            vendor_ids.add(vid)

    if not vendor_ids:
        return

    contacts = client.get_contacts(vendor_ids)
    contact_map = {}
    for c in contacts:
        cid = c.get("id") or c.get("contactId")
        if cid:
            # Build display name from available fields
            first = (c.get("firstName") or "").strip()
            last = (c.get("lastName") or "").strip()
            company = (c.get("companyName") or c.get("company") or "").strip()
            name = f"{first} {last}".strip() or company or ""
            if name:
                contact_map[str(cid)] = name

    for wo in work_orders:
        vid = wo.get("vendorContactId")
        if vid:
            wo["_vendor_name"] = contact_map.get(str(vid), "")


def _resolve_scheduled_date(wo, visits_index):
    """
    Derive scheduled_date for a work order.

    Priority:
    1. Visit logDate from the visits index.
    2. vendorWorkOrderRequest.scheduledConfirmedAt on the WO itself.
    """
    wo_id = wo.get("id")
    visits = visits_index.get(wo_id, [])
    for visit in visits:
        log_date = visit.get("logDate")
        if log_date:
            return log_date

    # Fallback: scheduledConfirmedAt from the vendor request block
    vendor_req = wo.get("vendorWorkOrderRequest") or {}
    confirmed = vendor_req.get("scheduledConfirmedAt")
    if confirmed:
        # May be datetime — take date portion
        return confirmed[:10] if len(confirmed) >= 10 else confirmed

    return None


def _find_latest_complete_visit(wo_id, visits_index):
    """Return the visit dict with the latest logDate among Complete visits.

    /visits ordering is unspecified, so we compare all visits and select by
    logDate.  Returns None when no visit has status=Complete with a logDate.

    This is the single source for both the completed_date and the
    work_performed_note — they must come from the same visit.
    """
    visits = visits_index.get(wo_id, [])
    best_visit = None
    best_date = None
    for visit in visits:
        if (visit.get("status") or "").lower() == "complete":
            log_date = visit.get("logDate")
            if log_date:
                date_str = log_date[:10] if len(log_date) >= 10 else log_date
                if best_date is None or date_str > best_date:
                    best_date = date_str
                    best_visit = visit
    return best_visit


def _resolve_completed_date(wo, activity_index, visits_index):
    """
    Derive completed_date for a work order.

    Priority:
    1. Visit with status=Complete → logDate (the on-site work date).
    2. vendor_work_completed event → details.markedCompleteAt.
    3. status_changed → "closed" event timestamp.

    Why logDate is first: logDate is when the technician was physically on-site
    and marked the visit complete.  markedCompleteAt and the status_changed
    → closed timestamp are paperwork dates that routinely lag the real work by
    days.  For "Completed This Week" bucketing we want the date the work
    actually happened, not when the office filed it.

    When a work order has multiple visits with status=Complete, we take the
    LATEST logDate.  /visits ordering is unspecified, so selecting the first
    match would be non-deterministic.
    """
    wo_id = wo.get("id")

    # 1. Visit completion date via shared helper.
    visit = _find_latest_complete_visit(wo_id, visits_index)
    if visit:
        log_date = visit["logDate"]
        return log_date[:10] if len(log_date) >= 10 else log_date

    # 2. vendor_work_completed event
    logs = activity_index.get(wo_id, [])
    for log in logs:
        event_type = log.get("eventType") or log.get("type") or ""
        if event_type == "vendor_work_completed":
            details = log.get("details") or {}
            marked = details.get("markedCompleteAt")
            if marked:
                return marked[:10] if len(marked) >= 10 else marked

    # 3. Fallback: status_changed to "closed"
    for log in logs:
        event_type = log.get("eventType") or log.get("type") or ""
        if event_type == "status_changed":
            details = log.get("details") or {}
            new_status = details.get("newStatus") or details.get("to") or ""
            if new_status == "closed":
                ts = log.get("createdAt") or log.get("timestamp") or ""
                if ts:
                    return ts[:10] if len(ts) >= 10 else ts

    return None


def _resolve_cancelled_date(wo, activity_index):
    """
    Derive cancelled_date for a work order.

    Returns the timestamp of the status_changed → "cancelled" event, or None
    if no such event exists.  Does NOT fall back to updatedAt — updatedAt
    changes for any reason and would give every WO a spurious cancelled_date.
    """
    wo_id = wo.get("id")
    logs = activity_index.get(wo_id, [])

    for log in logs:
        event_type = log.get("eventType") or log.get("type") or ""
        if event_type == "status_changed":
            details = log.get("details") or {}
            new_status = details.get("newStatus") or details.get("to") or ""
            if new_status == "cancelled":
                ts = log.get("createdAt") or log.get("timestamp") or ""
                if ts:
                    return ts[:10] if len(ts) >= 10 else ts

    return None


def _is_recurring(wo):
    """
    Return True when the work order should be treated as recurring.

    Matches: workOrderType == "recurring", starts with "inspection",
    or source == "mason:recurring".
    """
    wo_type = (wo.get("workOrderType") or "").lower()
    source = (wo.get("source") or "").lower()
    return (
        wo_type == "recurring"
        or wo_type.startswith("inspection")
        or source == "mason:recurring"
    )


_LAWN_ISH_VENDOR_TYPES = frozenset({"landscaping", "landscaper", "tree_service"})

_LAWN_ISH_DESC = re.compile(r"lawn|grass|mow|landscap", re.IGNORECASE)


def _is_lawn_ish(wo):
    """Return True when the work order is lawn/landscaping-related.

    Matches on description keywords OR workOrderReview.vendorType.
    Broad by design — the recurring gate does the safety work; this
    function is never used alone to drop a work order.
    """
    desc = wo.get("description") or ""
    if _LAWN_ISH_DESC.search(desc):
        return True
    review = wo.get("workOrderReview") or {}
    vendor_type = (review.get("vendorType") or "").lower()
    return vendor_type in _LAWN_ISH_VENDOR_TYPES


_CADENCE_DESC = re.compile(
    r"bi-weekly|biweekly|weekly|every\s+week|every\s+two\s+weeks"
    r"|2x\s+per\s+month|twice\s+per\s+month|monthly|per\s+month",
    re.IGNORECASE,
)


def _has_cadence(wo):
    """Return True when the description contains a scheduling cadence phrase."""
    desc = wo.get("description") or ""
    return bool(_CADENCE_DESC.search(desc))


def _resolve_work_performed_note(wo_id, visits_index):
    """Extract the technician's work description from the same visit that
    supplied the completed_date.

    Uses ``_find_latest_complete_visit`` so the note and the date are
    guaranteed to come from the same visit record.
    """
    visit = _find_latest_complete_visit(wo_id, visits_index)
    if not visit:
        return None
    work_notes = visit.get("workNotes") or {}
    note = (work_notes.get("descriptionOfWorkPerformed") or "").strip()
    return note or None


def _resolve_approved_estimate(wo_id, estimates_index):
    """Return the approved estimate amount as a Decimal (dollars), or None.

    Predicate: status == "approved_for_work" AND approvedAmount is not None.

    When multiple estimates match (a revised estimate approved after the
    first), take the LATEST by pmApprovedAt, falling back to updatedAt
    if pmApprovedAt ties or is absent.  /estimates ordering is not
    guaranteed.

    approvedAmount is stored as integer cents per the Mason spec:
    "Stored approved amount in integer cents; may differ from the quote."
    """
    estimates = estimates_index.get(wo_id, [])
    approved = [
        e for e in estimates
        if e.get("status") == "approved_for_work"
        and e.get("approvedAmount") is not None
    ]
    if not approved:
        return None

    if len(approved) > 1:
        # Tiebreak: latest pmApprovedAt, then updatedAt.
        def _sort_key(e):
            return (e.get("pmApprovedAt") or "", e.get("updatedAt") or "")
        approved.sort(key=_sort_key, reverse=True)
        logger.info(
            "mason_multiple_approved_estimates",
            extra={
                "work_order_id": wo_id,
                "count": len(approved),
                "selected_id": approved[0].get("id"),
                "selected_pmApprovedAt": approved[0].get("pmApprovedAt"),
                "selected_amount_cents": approved[0].get("approvedAmount"),
            },
        )

    return Decimal(approved[0]["approvedAmount"]) / 100


def fetch_mason_work_order_sweep(period_start, period_end):
    """
    Execute the full Mason batch sweep.  Called ONCE before the portfolio loop.

    Returns a dict ready to pass into get_portfolio_work_order_data_mason():
    {
        "work_orders": [enriched WO dicts],
        "visits_index": {mason_wo_id: [visit_records]},
        "activity_index": {mason_wo_id: [activity_records]},
        "estimates_index": {mason_wo_id: [estimate_records]},
        "rentvine_lookup": {rentvine_id: WorkOrder},
    }

    Each enriched WO dict has:
        - all raw Mason fields
        - _rentvine_id: int or None (join key)
        - _local_wo: WorkOrder instance or None
        - _vendor_name: str
        - _scheduled_date: str (YYYY-MM-DD) or None
        - _completed_date: str (YYYY-MM-DD) or None
        - _cancelled_date: str (YYYY-MM-DD) or None
        - _work_performed_note: str or None
        - _approved_estimate_amount: Decimal or None (dollars)
        - _is_recurring: bool
        - _mason_status: raw Mason status string
        - _mapped_bucket: "open" | "scheduled" | "completed" | "cancelled"
                          | "excluded" | "rentvine_fallback"
    """
    client = MasonClient()
    rentvine_lookup = _build_rentvine_lookup()

    # ── Part 1: unbounded fetch for open statuses ────────────────────
    part1_records = []
    for status in _OPEN_STATUSES:
        records = client.get_work_orders(status=status)
        part1_records.extend(records)
        logger.info(
            "mason_sweep_part1_status",
            extra={"status": status, "count": len(records)},
        )

    # ── Part 2: windowed fetch for completions/cancellations ─────────
    buffer_start = _date_to_utc_iso(period_start - timedelta(days=2))
    part2_records = client.get_work_orders(updated_after=buffer_start)
    if not part2_records:
        logger.error(
            "mason_sweep_part2_zero_records",
            extra={"updated_after": buffer_start},
        )
    logger.info(
        "mason_sweep_part2",
        extra={
            "updated_after": buffer_start,
            "count": len(part2_records),
        },
    )

    # ── Union + dedup by Mason id ────────────────────────────────────
    seen_ids = set()
    all_work_orders = []
    for wo in part1_records + part2_records:
        wo_id = wo.get("id")
        if wo_id and wo_id not in seen_ids:
            seen_ids.add(wo_id)
            all_work_orders.append(wo)

    logger.info(
        "mason_sweep_deduped",
        extra={
            "part1": len(part1_records),
            "part2": len(part2_records),
            "unique": len(all_work_orders),
        },
    )

    # ── Sub-resource sweeps ──────────────────────────────────────────

    # Activity logs: windowed only.  Activity logs derive completed_date
    # and cancelled_date, which are only needed for Part 2 (windowed)
    # work orders.  Part 1 open WOs never need activity logs.
    activity_logs = client.get_activity_logs(after=buffer_start)
    if not activity_logs:
        logger.error(
            "mason_activity_logs_zero_records",
            extra={"after": buffer_start},
        )
    activity_index = _index_by_work_order_id(activity_logs)

    # Visits: wide logDate sweep covers both recent and upcoming visits
    # for long-idle open WOs (a scheduled visit carries its date in
    # logDate).  180 days back from period_start catches anything
    # reasonable without downloading the full account history.
    visits_start = _date_to_utc_iso(period_start - timedelta(days=180))
    visits = client.get_visits(log_date_after=visits_start)
    if not visits:
        logger.error(
            "mason_visits_zero_records",
            extra={"log_date_after": visits_start},
        )
    visits_index = _index_by_work_order_id(visits)

    # For Part 1 open WOs still without a visit record, check whether
    # vendorWorkOrderRequest.scheduledConfirmedAt on the WO itself
    # provides the scheduled date (no extra call needed —
    # _resolve_scheduled_date already checks this fallback).  Only fetch
    # per-WO for those that have neither.
    visits_missing = set()
    for wo in part1_records:
        wo_id = wo.get("id")
        if not wo_id or wo_id in visits_index:
            continue
        vendor_req = wo.get("vendorWorkOrderRequest") or {}
        if vendor_req.get("scheduledConfirmedAt"):
            continue  # _resolve_scheduled_date will use this fallback
        visits_missing.add(wo_id)

    _VISIT_FETCH_CAP = 100
    if visits_missing:
        if len(visits_missing) > _VISIT_FETCH_CAP:
            logger.error(
                "mason_visit_gap_cap_exceeded",
                extra={
                    "missing_count": len(visits_missing),
                    "cap": _VISIT_FETCH_CAP,
                },
            )
        fetch_ids = list(visits_missing)[:_VISIT_FETCH_CAP]
        for wo_id in fetch_ids:
            per_wo_visits = client.get_visits(work_order_id=wo_id)
            if per_wo_visits:
                visits_index[wo_id] = per_wo_visits
        logger.info(
            "mason_visit_gap_fill",
            extra={
                "missing": len(visits_missing),
                "fetched": len(fetch_ids),
            },
        )

    # ── Estimates: unbounded (no date filter) ────────────────────────
    # Estimates are a small bounded set (~74 across the whole account,
    # one or two pages).  Open/Scheduled work orders are fetched without
    # a date lower bound (Part 1), so windowing estimates would silently
    # miss an estimate approved weeks ago on a long-standing open WO.
    # Fetching all estimates is cheaper than a windowed sweep plus a
    # gap-fill, and removes the window mismatch entirely.
    estimates = client.get_estimates()
    estimates_index = _index_by_work_order_id(estimates)
    logger.info(
        "mason_estimates_fetched",
        extra={"count": len(estimates)},
    )

    # ── Join to local WorkOrder records ──────────────────────────────
    # pms.workOrderId where pms.system == "rentvine" → WorkOrder.rentvine_id
    join_failures = []
    for wo in all_work_orders:
        pms = wo.get("pms") or {}
        rentvine_id = None
        if pms.get("system") == "rentvine":
            raw_id = pms.get("workOrderId")
            if raw_id is not None:
                try:
                    rentvine_id = int(raw_id)
                except (ValueError, TypeError):
                    pass

        local_wo = rentvine_lookup.get(rentvine_id) if rentvine_id else None
        wo["_rentvine_id"] = rentvine_id
        wo["_local_wo"] = local_wo

        if local_wo is None:
            reason = "no pms.workOrderId"
            if rentvine_id:
                reason = f"rentvine_id={rentvine_id} not found in active WorkOrders"
            elif pms.get("system") and pms.get("system") != "rentvine":
                reason = f"pms.system={pms.get('system')}, not rentvine"
            elif not pms.get("system"):
                reason = "no pms block"
            join_failures.append({
                "mason_id": wo.get("id"),
                "mason_status": wo.get("status"),
                "pms_system": pms.get("system"),
                "pms_work_order_id": pms.get("workOrderId"),
                "pms_work_order_number": pms.get("workOrderNumber"),
                "reason": reason,
                "description": (wo.get("description") or "")[:80],
            })

    if join_failures:
        logger.warning(
            "mason_join_failures",
            extra={
                "count": len(join_failures),
                "details": join_failures[:20],
            },
        )

    # ── Resolve vendor names ─────────────────────────────────────────
    _resolve_vendor_names(all_work_orders, client)

    # ── Derive dates and bucket assignment ───────────────────────────
    for wo in all_work_orders:
        wo["_scheduled_date"] = _resolve_scheduled_date(wo, visits_index)
        wo["_completed_date"] = _resolve_completed_date(
            wo, activity_index, visits_index,
        )
        wo["_cancelled_date"] = _resolve_cancelled_date(wo, activity_index)
        wo["_work_performed_note"] = _resolve_work_performed_note(
            wo.get("id"), visits_index,
        )
        wo["_approved_estimate_amount"] = _resolve_approved_estimate(
            wo.get("id"), estimates_index,
        )
        wo["_is_recurring"] = _is_recurring(wo)
        wo["_is_lawn_ish"] = _is_lawn_ish(wo)
        wo["_has_cadence"] = _has_cadence(wo)
        wo["_work_order_type"] = (wo.get("workOrderType") or "").lower()

        mason_status = (wo.get("status") or "").lower().strip()
        wo["_mason_status"] = mason_status

        if mason_status in _EXCLUDED_STATUSES:
            wo["_mapped_bucket"] = "excluded"
            logger.info(
                "mason_wo_excluded",
                extra={
                    "mason_id": wo.get("id"),
                    "status": mason_status,
                    "description": (wo.get("description") or "")[:80],
                },
            )
        elif mason_status in _RENTVINE_FALLBACK_STATUSES:
            wo["_mapped_bucket"] = "rentvine_fallback"
            logger.info(
                "mason_wo_rentvine_fallback",
                extra={
                    "mason_id": wo.get("id"),
                    "status": mason_status,
                    "rentvine_id": wo.get("_rentvine_id"),
                    "reason": "not_handled_by_mason",
                },
            )
        elif mason_status in {s for s in _OPEN_STATUSES}:
            if wo["_scheduled_date"]:
                wo["_mapped_bucket"] = "scheduled"
            else:
                wo["_mapped_bucket"] = "open"
        elif mason_status in _COMPLETED_STATUSES:
            wo["_mapped_bucket"] = "completed"
        elif mason_status in _CANCELLED_STATUSES:
            wo["_mapped_bucket"] = "cancelled"
        else:
            # Unrecognized status — fall back to Rentvine, log loudly
            wo["_mapped_bucket"] = "rentvine_fallback"
            logger.error(
                "mason_wo_unrecognized_status",
                extra={
                    "mason_id": wo.get("id"),
                    "status": mason_status,
                    "rentvine_id": wo.get("_rentvine_id"),
                    "description": (wo.get("description") or "")[:80],
                },
            )

    # Resolve rentvine_fallback bucket using local WorkOrder.derived_status
    for wo in all_work_orders:
        if wo["_mapped_bucket"] == "rentvine_fallback":
            local_wo = wo.get("_local_wo")
            if local_wo:
                rv_status = local_wo.derived_status
                if rv_status == "Cancelled":
                    wo["_mapped_bucket"] = "cancelled"
                elif rv_status == "Completed":
                    wo["_mapped_bucket"] = "completed"
                else:
                    # "Open" from Rentvine — check for scheduled date
                    if wo["_scheduled_date"]:
                        wo["_mapped_bucket"] = "scheduled"
                    else:
                        wo["_mapped_bucket"] = "open"
                logger.info(
                    "mason_wo_rentvine_resolved",
                    extra={
                        "mason_id": wo.get("id"),
                        "mason_status": wo["_mason_status"],
                        "rentvine_derived": rv_status,
                        "final_bucket": wo["_mapped_bucket"],
                    },
                )
            else:
                # No local record to fall back on — default to open so
                # it does not silently vanish from the owner email.
                wo["_mapped_bucket"] = "open"
                logger.warning(
                    "mason_wo_fallback_no_local",
                    extra={
                        "mason_id": wo.get("id"),
                        "mason_status": wo["_mason_status"],
                        "final_bucket": "open",
                    },
                )

    return {
        "work_orders": all_work_orders,
        "visits_index": visits_index,
        "activity_index": activity_index,
        "estimates_index": estimates_index,
        "rentvine_lookup": rentvine_lookup,
    }
