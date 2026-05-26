"""
Meld cost lookups via RentVine bill charges.

Replaces the old Expenditure-based cost queries with BillCharge aggregation
through the Bill -> Meld FK relationship.
"""

import json
import logging
from decimal import Decimal

from django.db.models import Sum

from accounting.models import BillCharge

logger = logging.getLogger(__name__)


def get_meld_cost(meld) -> Decimal | None:
    """
    Single-meld cost lookup. Returns total non-voided charge amount
    across all non-voided bills linked to the meld, or None if no charges.
    """
    result = BillCharge.objects.filter(
        bill__meld=meld,
        bill__is_voided=False,
        is_voided=False,
    ).aggregate(total=Sum("amount"))["total"]
    return result  # Decimal or None


def get_meld_costs(meld_ids: list[int]) -> dict[int, Decimal]:
    """
    Batch cost lookup. Returns {meld_pk: Decimal} for melds that have charges.
    Missing keys mean no charges (None semantics).
    """
    if not meld_ids:
        return {}

    rows = (
        BillCharge.objects.filter(
            bill__meld_id__in=meld_ids,
            bill__is_voided=False,
            is_voided=False,
        )
        .values("bill__meld_id")
        .annotate(total=Sum("amount"))
    )
    costs = {row["bill__meld_id"]: row["total"] for row in rows}

    _log_multi_ledger_bills(meld_ids)

    return costs


def _log_multi_ledger_bills(meld_ids: list[int]) -> None:
    """
    Detect bills whose charges span multiple ledgers and emit a structured
    log entry. Useful for flagging split-ledger billing anomalies.
    """
    from django.db.models import Count

    multi = (
        BillCharge.objects.filter(
            bill__meld_id__in=meld_ids,
            bill__is_voided=False,
            is_voided=False,
            primary_ledger_id__isnull=False,
        )
        .values("bill_id")
        .annotate(ledger_count=Count("primary_ledger_id", distinct=True))
        .filter(ledger_count__gt=1)
    )

    for row in multi:
        logger.warning(
            "multi_ledger_bill %s",
            json.dumps(
                {"bill_id": row["bill_id"], "ledger_count": row["ledger_count"]}
            ),
        )
