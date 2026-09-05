"""
Selectors for the tenant notice cadence.

Pure query/aggregation functions. The caller (management command) provides
the RentvineClient; these functions use it to fetch transactions and
tenant emails.

Transaction field names verified from:
  - comms/selectors.py (chargeAccountID, transactionTypeID, isVoided, amount,
    amountPaid, leaseID, datePosted, propertyID, unitID)
  - comms/distribution_services.py (pagination pattern, isinstance(data, list) guard)
  - integrations/management/commands/dump_rentvine_transactions.py
"""

import logging
from collections import defaultdict
from decimal import Decimal

from django.conf import settings

from comms.selectors import _is_voided, _safe_decimal
from core.models import Lease, Unit

logger = logging.getLogger(__name__)


def _fetch_all_transactions(client, *, as_of_date, dry_run=False):
    """
    Paginate /accounting/transactions and keep records with
    datePosted <= as_of_date.  No server-side date filter exists
    (confirmed: integrations/rentvine/services.py:763-764).

    Uses manual client.get() pagination matching the pattern in
    comms/distribution_services.py:34-86.

    In dry_run mode, collects diagnostic counters per page.
    """
    records = []
    page = 1
    page_size = 100
    total_scanned = 0
    as_of_str = str(as_of_date)

    # Dry-run diagnostics
    voided_value_forms = set() if dry_run else None

    while True:
        data = client.get(
            "/accounting/transactions",
            params={"page": page, "pageSize": page_size},
        )
        if not isinstance(data, list) or not data:
            break

        page_kept = 0
        for record in data:
            total_scanned += 1
            tx = (
                record.get("transaction", record)
                if isinstance(record, dict)
                else record
            )
            date_posted = tx.get("datePosted") or ""
            if date_posted <= as_of_str:
                records.append(record)
                page_kept += 1

            # Correction O: collect isVoided value forms in dry-run
            if dry_run and voided_value_forms is not None:
                raw_voided = tx.get("isVoided")
                voided_value_forms.add(repr(raw_voided))

        if dry_run:
            logger.info(
                "tenant_notice_fetch_page",
                extra={
                    "page": page,
                    "page_records": len(data),
                    "page_kept": page_kept,
                    "running_total": len(records),
                },
            )

        # Exhaustive scan - no early exit. A short page means the last page.
        if len(data) < page_size:
            break
        page += 1

    logger.info(
        "tenant_notice_fetch_transactions",
        extra={
            "as_of": as_of_str,
            "total_scanned": total_scanned,
            "kept": len(records),
            "pages": page,
        },
    )

    diagnostics = {}
    if dry_run and voided_value_forms is not None:
        diagnostics["voided_value_forms"] = sorted(voided_value_forms)

    return records, diagnostics


def get_delinquent_leases(as_of_date, client, *, dry_run=False):
    """
    Identify leases with outstanding tenant-portion balances.

    Does NOT filter holds - that is the caller's responsibility so held
    leases remain visible in dry-run output.

    Returns a dict:
        {
            "delinquent": [
                {
                    "rentvine_lease_id": int,
                    "balance": Decimal,
                    "property_address": str,
                    "rentvine_property_id": int | None,
                    "rentvine_unit_id": int | None,
                    "lease_status_id": int | None,
                },
                ...
            ],
            "skipped_no_lease_id": int,
            "skipped_voided": int,
            "skipped_below_minimum": int,
            "skipped_no_address": int,
            "skipped_status_excluded": int,
            "skipped_status_unknown": int,
            "total_charges_scanned": int,
            "diagnostics": dict,
        }

    Arrears scope: total outstanding across ALL unpaid charges
    (amount - amountPaid) with datePosted <= as_of_date.

    Lease status gate: only leases whose local Lease.lease_status_id is
    in settings.TENANT_NOTICE_LEASE_STATUS_IDS. Unknown status -> skip.
    """
    transactions, fetch_diagnostics = _fetch_all_transactions(
        client, as_of_date=as_of_date, dry_run=dry_run
    )

    # Build string set from integer settings (Correction A)
    tenant_account_ids = {
        str(a) for a in getattr(settings, "TENANT_PORTION_ACCOUNT_IDS", {13, 25})
    }
    minimum_balance = getattr(
        settings, "TENANT_NOTICE_MINIMUM_BALANCE", Decimal("100.00")
    )
    allowed_status_ids = getattr(
        settings, "TENANT_NOTICE_LEASE_STATUS_IDS", {2}
    )

    # Accumulate balance per lease
    lease_balance = defaultdict(Decimal)
    lease_property = {}  # rentvine_lease_id -> rentvine_property_id
    lease_unit = {}      # rentvine_lease_id -> rentvine_unit_id

    skipped_no_lease_id = 0
    skipped_voided = 0
    total_charges_scanned = 0

    for record in transactions:
        tx = (
            record.get("transaction", record)
            if isinstance(record, dict)
            else record
        )

        # Only type 1 = charge
        if str(tx.get("transactionTypeID")) != "1":
            continue

        # Only tenant-portion accounts
        if str(tx.get("chargeAccountID") or "") not in tenant_account_ids:
            continue

        total_charges_scanned += 1

        # Skip voided
        if _is_voided(tx):
            skipped_voided += 1
            continue

        # Must have a leaseID
        lease_id = tx.get("leaseID")
        if lease_id is None or str(lease_id) == "":
            skipped_no_lease_id += 1
            continue

        lease_id_int = int(lease_id)
        amount = _safe_decimal(tx.get("amount"))
        amount_paid = _safe_decimal(tx.get("amountPaid"))
        outstanding = amount - amount_paid

        lease_balance[lease_id_int] += outstanding

        # Track property/unit for address resolution
        prop_id = tx.get("propertyID")
        if prop_id is not None and str(prop_id) != "":
            lease_property.setdefault(lease_id_int, int(prop_id))
        unit_id = tx.get("unitID")
        if unit_id is not None and str(unit_id) != "":
            lease_unit.setdefault(lease_id_int, int(unit_id))

    # Build local lease status index (Correction Q)
    local_leases = {
        lv[0]: lv[1]
        for lv in Lease.objects.filter(
            rentvine_id__in=list(lease_balance.keys())
        ).values_list("rentvine_id", "lease_status_id")
    }

    delinquent = []
    skipped_below_minimum = 0
    skipped_no_address = 0
    skipped_status_excluded = 0
    skipped_status_unknown = 0

    for rv_lease_id, balance in lease_balance.items():
        # R5: $100 or less gets NO notice
        if balance <= minimum_balance:
            skipped_below_minimum += 1
            continue

        # Lease status gate (Correction Q)
        status_id = local_leases.get(rv_lease_id)
        if status_id is None:
            skipped_status_unknown += 1
            logger.warning(
                "tenant_notice_status_unknown",
                extra={
                    "rentvine_lease_id": rv_lease_id,
                    "reason": "not_in_local_db_or_null_status",
                },
            )
            continue
        if status_id not in allowed_status_ids:
            skipped_status_excluded += 1
            continue

        # Resolve address: Unit.display_address first, Property fallback
        address = ""
        rv_unit_id = lease_unit.get(rv_lease_id)
        rv_prop_id = lease_property.get(rv_lease_id)

        if rv_unit_id:
            unit = Unit.objects.filter(rentvine_id=rv_unit_id).first()
            if unit:
                address = unit.display_address

        if not address and rv_prop_id:
            from core.models import Property

            prop = Property.objects.filter(rentvine_id=rv_prop_id).first()
            if prop:
                address = prop.address_line_1 or ""

        # No address = skip, never send "Property 137"
        if not address:
            skipped_no_address += 1
            logger.error(
                "tenant_notice_no_address",
                extra={
                    "rentvine_lease_id": rv_lease_id,
                    "rentvine_property_id": rv_prop_id,
                    "rentvine_unit_id": rv_unit_id,
                    "balance": str(balance),
                },
            )
            continue

        delinquent.append({
            "rentvine_lease_id": rv_lease_id,
            "balance": balance,
            "property_address": address,
            "rentvine_property_id": rv_prop_id,
            "rentvine_unit_id": rv_unit_id,
            "lease_status_id": status_id,
        })

    return {
        "delinquent": delinquent,
        "skipped_no_lease_id": skipped_no_lease_id,
        "skipped_voided": skipped_voided,
        "skipped_below_minimum": skipped_below_minimum,
        "skipped_no_address": skipped_no_address,
        "skipped_status_excluded": skipped_status_excluded,
        "skipped_status_unknown": skipped_status_unknown,
        "total_charges_scanned": total_charges_scanned,
        "diagnostics": fetch_diagnostics,
    }


def _extract_tenant_name(data):
    """
    Extract a display name from a tenant API record.

    Probes the same field names as integrations/rentvine/mappers.py:483-487.
    Returns the name string, or None if nothing usable found.
    """
    # The API wraps each record: {"leaseTenant": {...}, "contact": {...}}
    contact = data
    if "contact" in data and isinstance(data["contact"], dict):
        contact = data["contact"]

    first = str(contact.get("firstName") or contact.get("first_name") or "").strip()
    last = str(contact.get("lastName") or contact.get("last_name") or "").strip()
    full = str(
        contact.get("name")
        or contact.get("fullName")
        or contact.get("full_name")
        or contact.get("contactName")
        or ""
    ).strip()

    if full:
        return full
    if first or last:
        return f"{first} {last}".strip()
    return None


def _extract_tenant_email(data):
    """
    Extract email from a tenant API record.

    Probes the same field shapes as integrations/rentvine/mappers.py:489-496.
    Returns lowered/stripped email string, or empty string.
    """
    contact = data
    if "contact" in data and isinstance(data["contact"], dict):
        contact = data["contact"]

    emails_field = contact.get("emails")
    if isinstance(emails_field, list) and emails_field:
        first = emails_field[0]
        if isinstance(first, dict):
            email = (first.get("email") or first.get("emailAddress") or "")
        elif isinstance(first, str):
            email = first
        else:
            email = ""
        email = email.strip().lower()
        if email:
            return email
    elif isinstance(emails_field, str) and emails_field:
        return emails_field.strip().lower()

    return (
        str(contact.get("email") or contact.get("emailAddress") or "")
        .strip()
        .lower()
    )


def _extract_tenant_contact_id(data):
    """Extract contactID from a tenant API record."""
    contact = data
    if "contact" in data and isinstance(data["contact"], dict):
        contact = data["contact"]
    cid = contact.get("contactID") or contact.get("contact_id") or contact.get("id")
    if cid is not None:
        try:
            return int(cid)
        except (ValueError, TypeError):
            pass
    return None


def resolve_lease_tenant_emails(rentvine_lease_id, client):
    """
    Resolve tenant email addresses for a lease.

    R7: API first, local second.
    1. Call GET /leases/{id}/tenants.
    2. If the API call fails, fall back to local DB and log loudly.

    R3: missing_recipients stores names, not contact IDs.
    Falls back to "Contact #<id>" when no name is available.

    Returns:
        {
            "emails": [str, ...],
            "missing_recipients": [str, ...],  # human-readable names
        }
    """
    # --- Attempt 1: API ---
    api_ok = False
    try:
        data = client.get(f"/leases/{rentvine_lease_id}/tenants")
        if isinstance(data, list):
            tenant_list = data
        elif isinstance(data, dict):
            tenant_list = data.get("data", data.get("results", []))
            if not isinstance(tenant_list, list):
                tenant_list = []
        else:
            tenant_list = []
        api_ok = True
    except Exception:
        logger.exception(
            "tenant_notice_api_tenant_fetch_failed",
            extra={"rentvine_lease_id": rentvine_lease_id},
        )
        tenant_list = []

    if api_ok and tenant_list:
        emails = []
        missing = []
        for tenant in tenant_list:
            email = _extract_tenant_email(tenant)
            if email:
                emails.append(email)
            else:
                name = _extract_tenant_name(tenant)
                contact_id = _extract_tenant_contact_id(tenant)
                label = name or (f"Contact #{contact_id}" if contact_id else "Unknown tenant")
                missing.append(label)
        return {"emails": emails, "missing_recipients": missing}

    # --- Attempt 2: local DB fallback ---
    if api_ok and not tenant_list:
        # API succeeded but returned empty - not an error, just no tenants
        logger.warning(
            "tenant_notice_api_returned_no_tenants",
            extra={"rentvine_lease_id": rentvine_lease_id},
        )
    else:
        # API failed - log loudly per R7
        logger.error(
            "tenant_notice_using_local_fallback",
            extra={
                "rentvine_lease_id": rentvine_lease_id,
                "reason": "api_call_failed",
            },
        )

    try:
        lease = Lease.objects.get(rentvine_id=rentvine_lease_id)
        tenants = lease.tenants.all()
        if tenants.exists():
            emails = []
            missing = []
            for t in tenants:
                email = (t.email or "").strip().lower()
                if email:
                    emails.append(email)
                else:
                    name = t.name or (
                        f"{t.first_name} {t.last_name}".strip()
                    ) or f"Contact #{t.rentvine_contact_id}"
                    missing.append(name)
            if emails:
                return {"emails": emails, "missing_recipients": missing}
    except Lease.DoesNotExist:
        pass

    return {"emails": [], "missing_recipients": []}
