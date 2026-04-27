"""
RentVine data source for monthly owner reports.
Combines local DB queries with a live API call for tenant notes.
"""
import logging
from datetime import timedelta

from django.db.models import Q, Sum
from django.utils import timezone

logger = logging.getLogger(__name__)


def get_owner_properties(owner) -> list:
    """Return active properties across all of the owner's portfolios."""
    from properties.models import Property

    portfolio_ids = owner.portfolios.values_list("id", flat=True)
    return list(
        Property.objects.filter(
            portfolio_id__in=portfolio_ids,
            is_active=True,
        ).select_related("portfolio")
    )


def get_active_lease(property_obj):
    """Return the most recent active lease (primary_lease_status=2) for the property."""
    from leasing.models import Lease

    return (
        Lease.objects.filter(
            property=property_obj,
            primary_lease_status=2,  # Active
        )
        .prefetch_related("tenants")
        .order_by("-start_date")
        .first()
    )


def get_upcoming_lease(property_obj):
    """Return a pending/future lease (primary_lease_status=1) for the property."""
    from leasing.models import Lease

    return (
        Lease.objects.filter(
            property=property_obj,
            primary_lease_status=1,  # Pending
        )
        .prefetch_related("tenants")
        .order_by("-start_date")
        .first()
    )


def get_financial_summary(property_obj, month_start, month_end) -> dict:
    """
    Summarise lease charges and payments from accounting.Transaction for this
    property during [month_start, month_end).

    Returns:
        {charged, paid, outstanding_balance, has_data: bool}
    """
    from accounting.models import Transaction

    base_qs = Transaction.objects.filter(
        property=property_obj,
        is_voided=False,
        date_posted__gte=month_start,
        date_posted__lt=month_end,
    )

    charged_agg = base_qs.filter(transaction_type=1).aggregate(total=Sum("amount"))
    paid_agg = base_qs.filter(transaction_type=2).aggregate(total=Sum("amount"))

    charged = float(charged_agg["total"] or 0)
    paid = float(paid_agg["total"] or 0)
    outstanding = max(charged - paid, 0)

    return {
        "charged": charged,
        "paid": paid,
        "outstanding_balance": outstanding,
        "has_data": charged > 0 or paid > 0,
    }


def get_tenant_notes(lease, since_days: int = 45) -> list:
    """
    Fetch recent tenant notes from the live RentVine API.
    Uses /leases/{rentvine_id}/notes for the lease record.
    Returns a list of {note_text, created_at} dicts, or [] on any error.
    """
    if lease is None:
        return []

    try:
        from integrations.rentvine.client import RentvineClient

        client = RentvineClient()
        cutoff = timezone.now() - timedelta(days=since_days)
        data = client.get(f"leases/{lease.rentvine_id}/notes")

        # API may return list or {"data": [...]}
        if isinstance(data, list):
            raw_notes = data
        elif isinstance(data, dict):
            raw_notes = data.get("data") or data.get("results") or []
        else:
            raw_notes = []

        notes = []
        for n in raw_notes:
            created_raw = n.get("createdAt") or n.get("created_at") or ""
            try:
                from django.utils.dateparse import parse_datetime
                created_dt = parse_datetime(created_raw)
                if created_dt and created_dt < cutoff:
                    continue
            except Exception:
                pass

            text = n.get("note") or n.get("body") or n.get("text") or ""
            if text:
                notes.append({
                    "note_text": str(text)[:500],
                    "created_at": created_raw[:10],
                })

        return notes

    except Exception as exc:
        from integrations.rentvine.client import RentvineAPIError
        if isinstance(exc, RentvineAPIError) and exc.status_code == 404:
            # 404 means this lease simply has no notes — expected, not an error
            return []
        logger.warning(
            "Could not fetch tenant notes for lease %s",
            lease.rentvine_id,
            exc_info=True,
        )
        return []
