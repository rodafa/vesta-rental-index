"""
RentVine data source for monthly owner reports.
Combines local DB queries with a live API call for tenant notes.
"""
import logging
from datetime import timedelta

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


def _extract_transactions(response):
    """Extract the transaction list from a RentVine API response."""
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        return response.get("data") or response.get("results") or []
    return []


def _sum_charges_and_payments(transactions) -> tuple:
    """Sum lease charges (type 1) and lease payments (type 2) from raw API data."""
    charged = 0.0
    paid = 0.0
    for txn in transactions:
        t = txn.get("transaction", txn) if isinstance(txn, dict) else txn
        if str(t.get("isVoided", "0")) in ("1", "true", "True", True):
            continue
        try:
            txn_type = int(t.get("transactionTypeID", 0))
            amount = float(t.get("amount", 0))
        except (ValueError, TypeError):
            continue
        if txn_type == 1:  # Lease Charge
            charged += amount
        elif txn_type == 2:  # Lease Payment
            paid += amount
    return charged, paid


def get_financial_summary(property_obj, month_start, month_end) -> dict:
    """
    Fetch lease charges and payments from the live RentVine API for this
    property during [month_start, month_end).

    Returns:
        {total_income, total_collected, outstanding, all_time_overdue, has_data: bool}
    """
    empty = {
        "total_income": 0.0,
        "total_collected": 0.0,
        "outstanding": 0.0,
        "all_time_overdue": 0.0,
        "has_data": False,
    }

    if not property_obj.rentvine_id:
        return empty

    try:
        from integrations.rentvine.client import RentvineClient

        client = RentvineClient()
        month_end_inclusive = (month_end - timedelta(days=1)).isoformat()

        # Period transactions
        response = client.get(
            "accounting/transactions/search",
            params={
                "propertyID": property_obj.rentvine_id,
                "datePostedMin": month_start.isoformat(),
                "datePostedMax": month_end_inclusive,
                "pageSize": 500,
            },
        )
        txns = _extract_transactions(response)
        charged, paid = _sum_charges_and_payments(txns)
        outstanding = max(charged - paid, 0)

        # All-time running balance (charges minus payments across all dates)
        all_response = client.get(
            "accounting/transactions/search",
            params={
                "propertyID": property_obj.rentvine_id,
                "pageSize": 1000,
            },
        )
        all_txns = _extract_transactions(all_response)
        all_charged, all_paid = _sum_charges_and_payments(all_txns)
        all_time_overdue = max(all_charged - all_paid, 0)

        return {
            "total_income": charged,
            "total_collected": paid,
            "outstanding": outstanding,
            "all_time_overdue": all_time_overdue,
            "has_data": charged > 0 or paid > 0,
        }

    except Exception:
        logger.warning(
            "Could not fetch financial summary for property %s",
            property_obj.pk,
            exc_info=True,
        )
        return empty


def get_portfolio_financial_summary(portfolio, month_start, month_end) -> dict:
    """
    Fetch total payments received across all properties in the portfolio
    for [month_start, month_end) from the live RentVine API.

    Note: The RentVine ``portfolioID`` query param does not filter results,
    so we paginate through all type-2 (Lease Payment) transactions for the
    period and match the portfolio client-side.

    Returns:
        {total_collected, reserve_amount, additional_reserve_amount, hold_distributions}
    """
    result = {
        "total_collected": 0.0,
        "reserve_amount": float(portfolio.reserve_amount or 0),
        "additional_reserve_amount": float(portfolio.additional_reserve_amount or 0),
        "hold_distributions": bool(portfolio.hold_distributions),
    }

    if not portfolio.rentvine_id:
        return result

    try:
        from integrations.rentvine.client import RentvineClient

        client = RentvineClient()
        month_end_inclusive = (month_end - timedelta(days=1)).isoformat()
        target_id = str(portfolio.rentvine_id)

        total = 0.0
        page = 1
        page_size = 500

        while True:
            response = client.get(
                "accounting/transactions/search",
                params={
                    "transactionTypeID": 2,  # Lease Payment
                    "datePostedMin": month_start.isoformat(),
                    "datePostedMax": month_end_inclusive,
                    "page": page,
                    "pageSize": page_size,
                },
            )
            txns = _extract_transactions(response)
            if not txns:
                break

            for txn in txns:
                port_obj = txn.get("portfolio") or {}
                if str(port_obj.get("portfolioID")) != target_id:
                    continue
                t = txn.get("transaction", txn) if isinstance(txn, dict) else txn
                if str(t.get("isVoided", "0")) in ("1", "true", "True", True):
                    continue
                try:
                    total += float(t.get("amount", 0))
                except (ValueError, TypeError):
                    pass

            if len(txns) < page_size:
                break
            page += 1

        result["total_collected"] = total

    except Exception:
        logger.warning(
            "Could not fetch portfolio financial summary for portfolio %s",
            portfolio.pk,
            exc_info=True,
        )

    return result


def _empty_statement() -> dict:
    return {
        "statement_period": "",
        "beginning_balance": 0.0,
        "total_income": 0.0,
        "total_expenses": 0.0,
        "total_adjustments": 0.0,
        "ending_balance": 0.0,
        "total_distribution": 0.0,
    }


def get_portfolio_statement(portfolio_rentvine_id) -> dict:
    """
    Fetch the most recent owner statement for a portfolio from RentVine.

    Calls GET /portfolios/{id}/statements?type=owner&limit=1&sort=-endDate
    and parses the statement object.

    Returns a dict with period string and all financial totals.
    Returns all-zeros dict on any error or when no statement exists.
    """
    try:
        from integrations.rentvine.client import RentvineClient

        client = RentvineClient()
        response = client.get(
            f"portfolios/{portfolio_rentvine_id}/statements",
            params={"type": "owner", "limit": 1, "sort": "-endDate"},
        )

        if isinstance(response, list):
            statements = response
        elif isinstance(response, dict):
            statements = response.get("data") or response.get("results") or []
        else:
            statements = []

        if not statements:
            return _empty_statement()

        raw = statements[0]
        data = raw.get("statement") or raw

        return {
            "statement_period": (
                f"{data.get('startDate', '')} - {data.get('endDate', '')}"
            ).strip(" -"),
            "beginning_balance": float(data.get("beginningBalance") or 0),
            "total_income": float(data.get("totalIncomeAmount") or 0),
            "total_expenses": float(data.get("totalExpenseAmount") or 0),
            "total_adjustments": float(data.get("totalAdjustmentAmount") or 0),
            "ending_balance": float(data.get("endingBalance") or 0),
            "total_distribution": float(data.get("totalDistributionAmount") or 0),
        }

    except Exception:
        logger.warning(
            "Could not fetch statement for portfolio %s",
            portfolio_rentvine_id,
            exc_info=True,
        )
        return _empty_statement()


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


def get_vacancy_status(property_obj) -> str:
    """
    Check the most recent DailyUnitSnapshot for this property's units to
    determine marketing status.

    Returns:
        'active'     — at least one unit is actively being marketed
        'make_ready' — units are in make-ready, not yet marketed
        'vacant'     — vacant with no snapshot data
    """
    from market.models import DailyUnitSnapshot

    latest_status = (
        DailyUnitSnapshot.objects
        .filter(unit__property=property_obj)
        .order_by("-snapshot_date")
        .values_list("status", flat=True)
        .first()
    )

    if latest_status == "active":
        return "active"
    elif latest_status == "make_ready":
        return "make_ready"
    return "vacant"
