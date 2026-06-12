"""
Selectors for the owner distribution email product.

Pure query functions — no I/O. They receive a pre-fetched list of RentVine
transaction dicts and filter/aggregate in memory.

The caller (management command) fetches the full transaction set once per run,
then passes it to these selectors for each portfolio.
"""

import logging
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.conf import settings

from core.models import Property

logger = logging.getLogger(__name__)


def _safe_decimal(value):
    """Convert to Decimal or return Decimal('0')."""
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _is_voided(tx):
    """Return True if the transaction is voided. Type-safe: handles int, str, bool."""
    return str(tx.get("isVoided", "0")) == "1"


def get_rent_by_property(portfolio, month_start, month_end, transactions):
    """
    Filter pre-fetched transactions for rent income postings on this portfolio.

    Returns a list of dicts, one per property with activity:
        [{"property_id": int, "property_address": str,
          "expected": Decimal, "collected": Decimal}]

    expected = sum(amount) over non-voided type-1 records on RENT_INCOME_ACCOUNT_IDS,
               datePosted within [month_start, month_end], for this portfolio.
    collected = sum(amountPaid) over those SAME records.

    Records with no propertyID are logged and skipped.
    """
    rent_account_ids = {
        str(a) for a in getattr(settings, "RENT_INCOME_ACCOUNT_IDS", {13, 14})
    }
    portfolio_rv_id = str(portfolio.rentvine_id)

    # Accumulate per property
    property_expected = defaultdict(Decimal)
    property_collected = defaultdict(Decimal)

    for record in transactions:
        tx = record.get("transaction", record) if isinstance(record, dict) else record

        # Filter: type 1, matching portfolio, matching account, date in range
        if str(tx.get("transactionTypeID")) != "1":
            continue
        if str(tx.get("portfolioID")) != portfolio_rv_id:
            continue
        if str(tx.get("chargeAccountID") or "") not in rent_account_ids:
            continue
        if _is_voided(tx):
            continue

        date_posted = tx.get("datePosted") or ""
        if not (str(month_start) <= date_posted <= str(month_end)):
            continue

        prop_id = tx.get("propertyID")
        if prop_id is None or str(prop_id) == "":
            logger.warning(
                "comms_distribution_rent_no_property_id",
                extra={
                    "transaction_id": tx.get("transactionID"),
                    "portfolio_id": portfolio_rv_id,
                    "amount": str(tx.get("amount")),
                    "date_posted": date_posted,
                },
            )
            continue

        prop_id_int = int(prop_id)
        property_expected[prop_id_int] += _safe_decimal(tx.get("amount"))
        property_collected[prop_id_int] += _safe_decimal(tx.get("amountPaid"))

    if not property_expected:
        return []

    # Resolve addresses from local Property model
    rv_ids = list(property_expected.keys())
    address_map = dict(
        Property.objects.filter(rentvine_id__in=rv_ids).values_list(
            "rentvine_id", "address_line_1"
        )
    )

    result = []
    for rv_id in sorted(rv_ids):
        result.append({
            "property_id": rv_id,
            "property_address": address_map.get(rv_id, f"Property {rv_id}"),
            "expected": property_expected[rv_id],
            "collected": property_collected[rv_id],
        })

    return result


def get_portfolio_distribution(portfolio, month_start, month_end, transactions):
    """
    Filter pre-fetched transactions for distribution postings on this portfolio.

    Returns dict:
        {"amount": Decimal, "date": date-string | None, "count": int}

    amount = sum of non-voided type-16 records on OWNER_DISTRIBUTION_ACCOUNT_ID.
    date = latest datePosted among matches (as YYYY-MM-DD string, or None).
    count = number of matching records.
    """
    dist_account_id = str(
        getattr(settings, "OWNER_DISTRIBUTION_ACCOUNT_ID", 10)
    )
    portfolio_rv_id = str(portfolio.rentvine_id)

    total = Decimal("0")
    latest_date = None
    count = 0

    for record in transactions:
        tx = record.get("transaction", record) if isinstance(record, dict) else record

        if str(tx.get("transactionTypeID")) != "16":
            continue
        if str(tx.get("portfolioID")) != portfolio_rv_id:
            continue
        if str(tx.get("chargeAccountID") or "") != dist_account_id:
            continue
        if _is_voided(tx):
            continue

        date_posted = tx.get("datePosted") or ""
        if not (str(month_start) <= date_posted <= str(month_end)):
            continue

        total += _safe_decimal(tx.get("amount"))
        count += 1

        if date_posted and (latest_date is None or date_posted > latest_date):
            latest_date = date_posted

    return {"amount": total, "date": latest_date, "count": count}


def get_undeposited_rent(balances):
    """
    Sum rent-only undeposited balances from a RentVine ledger balances response.

    Includes:
      - undepositedReceiptsBalance (lease payment receipts)
      - undepositedPrepaymentReceiptsBalance (prepayment receipts)

    Excludes the other 7 sub-fields (security deposits, liabilities, etc.).
    Returns Decimal.
    """
    receipts = _safe_decimal(balances.get("undepositedReceiptsBalance"))
    prepayment = _safe_decimal(balances.get("undepositedPrepaymentReceiptsBalance"))
    return receipts + prepayment
