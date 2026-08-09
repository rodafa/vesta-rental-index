"""
Distribution snapshot generation service.

Extracts the generate logic from the management command so it can be called
from a dashboard API endpoint or any other caller.

The four RentVine data-fetch helpers (_fetch_month_transactions,
_resolve_ledger_ids, _fetch_portfolio_balances, _fetch_lease_balances) moved
here verbatim from comms/management/commands/generate_portfolio_distributions.py.
"""

import logging
from decimal import Decimal

from django.utils import timezone

from comms.models import PortfolioDistributionSnapshot
from comms.selectors import get_undeposited_rent
from comms.services import (
    build_distribution_snapshot,
    upsert_distribution_snapshot,
)
from core.models import Portfolio
from integrations.rentvine.client import RentvineClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RentVine data-fetch helpers (moved verbatim from management command)
# ---------------------------------------------------------------------------


def _fetch_month_transactions(month_start, month_end):
    """
    Fetch all transactions from RentVine for the target month.

    Paginates the full /accounting/transactions endpoint and keeps
    only records with datePosted within [month_start, month_end].
    No type or account filtering — that's the selectors' job.

    Returns a list of raw transaction dicts.
    """
    client = RentvineClient()
    records = []
    page = 1
    page_size = 100
    total_scanned = 0

    month_start_str = str(month_start)
    month_end_str = str(month_end)

    while True:
        data = client.get(
            "/accounting/transactions",
            params={"page": page, "pageSize": page_size},
        )
        if not isinstance(data, list) or not data:
            break

        for record in data:
            total_scanned += 1
            tx = (
                record.get("transaction", record)
                if isinstance(record, dict)
                else record
            )
            date_posted = tx.get("datePosted") or ""
            if month_start_str <= date_posted <= month_end_str:
                records.append(record)

        if len(data) < page_size:
            break
        page += 1

    logger.info(
        "comms_distribution_fetch_transactions",
        extra={
            "month_start": month_start_str,
            "month_end": month_end_str,
            "total_scanned": total_scanned,
            "kept": len(records),
            "pages": page,
        },
    )
    return records


def _resolve_ledger_ids():
    """
    Fetch the portfolio → ledgerID mapping from RentVine once per run.

    GET /accounting/ledgers/search?ledgerTypeID=2 returns portfolio-type ledgers.
    Each record has objectID (= portfolio's RentVine internal ID) and ledgerID.

    Paginates the response so all ledgers are captured, not just the first page.

    Returns {portfolio_rentvine_id: ledger_id} dict.
    """
    client = RentvineClient()
    ledger_map = {}
    page = 1
    page_size = 100

    while True:
        data = client.get(
            "/accounting/ledgers/search",
            params={"ledgerTypeID": 2, "page": page, "pageSize": page_size},
        )

        records = data if isinstance(data, list) else data.get("data", data.get("results", []))
        if not records:
            break

        for record in records:
            obj = record.get("ledger", record) if isinstance(record, dict) else record
            obj_id = obj.get("objectID")
            ledger_id = obj.get("ledgerID")
            if obj_id and ledger_id:
                ledger_map[int(obj_id)] = int(ledger_id)

        if len(records) < page_size:
            break
        page += 1

    logger.info(
        "comms_distribution_ledger_map",
        extra={"ledger_count": len(ledger_map)},
    )
    return ledger_map


def _fetch_portfolio_balances(ledger_id):
    """
    Fetch balances for a single ledger from RentVine.

    GET /accounting/ledgers/{ledgerID}/balances
    Returns the raw balances dict.
    """
    client = RentvineClient()
    data = client.get(f"/accounting/ledgers/{ledger_id}/balances")
    # Response may be a dict directly or wrapped in a list/data key
    if isinstance(data, list) and len(data) == 1:
        return data[0]
    return data


def _fetch_lease_balances():
    """
    Fetch all active leases from RentVine and build a per-property
    tenant balance index.

    Paginates GET /leases/search, filters client-side for active lease
    statuses (2-5) with positive currentBalance.

    Returns {int(propertyID): Decimal} — sum of positive balances per property.
    """
    ACTIVE_STATUSES = {"2", "3", "4", "5"}
    client = RentvineClient()
    balance_index = {}
    page = 1
    page_size = 100
    total_scanned = 0

    while True:
        data = client.get(
            "/leases/search",
            params={"page": page, "pageSize": page_size},
        )
        if not isinstance(data, list) or not data:
            break

        for record in data:
            total_scanned += 1
            lease = record.get("lease", record) if isinstance(record, dict) else record
            status_id = str(lease.get("leaseStatusID", ""))
            if status_id not in ACTIVE_STATUSES:
                continue

            current_balance = lease.get("currentBalance")
            if current_balance is None:
                continue

            bal = Decimal(str(current_balance))
            if bal <= 0:
                continue

            prop = record.get("property", {})
            prop_id_raw = prop.get("propertyID")
            if prop_id_raw is None:
                continue

            prop_id = int(prop_id_raw)
            balance_index[prop_id] = balance_index.get(prop_id, Decimal("0")) + bal

        if len(data) < page_size:
            break
        page += 1

    logger.info(
        "comms_distribution_fetch_lease_balances",
        extra={
            "total_scanned": total_scanned,
            "properties_with_balance": len(balance_index),
            "pages": page,
        },
    )
    return balance_index


# ---------------------------------------------------------------------------
# Service function
# ---------------------------------------------------------------------------


def generate_distribution_snapshots(
    month_start,
    month_end,
    *,
    portfolio_rentvine_id=None,
    progress_cb=None,
):
    """
    Generate distribution snapshots for all active portfolios (or one).

    Fetches the month's RentVine transactions ONCE, resolves ledger IDs
    (paginated), fetches per-property tenant balances, then loops
    portfolios to build and upsert PortfolioDistributionSnapshot rows.

    Applies the undeposited freeze: if a snapshot already has
    undeposited_as_of set, the captured value is carried forward
    unchanged. Otherwise the live balance is fetched from RentVine.

    Args:
        month_start: First day of the target month.
        month_end: Last day of the target month.
        portfolio_rentvine_id: Optional single portfolio rentvine_id filter.
        progress_cb: Optional callable(event: str, payload: dict) for
            live progress reporting. Events:
                "portfolios_resolved" — {"count": int}
                "fetch_start"        — {"step": str}
                "fetch_end"          — {"step": str, "count": int}
                "portfolio_done"     — {outcome dict}
                "complete"           — {"created": int, "updated": int,
                                        "errors": int}

    Returns a dict:
        {
            "created": int,
            "updated": int,
            "errors": list[str],
            "outcomes": list[dict],
            "fetch_stats": {
                "transactions": int,
                "ledger_ids": int,
                "properties_with_balance": int,
            },
        }
    """

    def _emit(event, payload):
        if progress_cb is not None:
            progress_cb(event, payload)

    # Resolve portfolios
    if portfolio_rentvine_id is not None:
        portfolios = Portfolio.objects.filter(
            rentvine_id=portfolio_rentvine_id, is_active=True
        )
    else:
        portfolios = Portfolio.objects.filter(is_active=True).order_by("name")

    portfolio_list = list(portfolios)
    total = len(portfolio_list)

    _emit("portfolios_resolved", {"count": total})

    # Fetch all transactions for the month ONCE
    _emit("fetch_start", {"step": "transactions"})
    transactions = _fetch_month_transactions(month_start, month_end)
    _emit("fetch_end", {"step": "transactions", "count": len(transactions)})

    # Resolve ledger IDs once for undeposited balances
    _emit("fetch_start", {"step": "ledger_ids"})
    ledger_map = _resolve_ledger_ids()
    _emit("fetch_end", {"step": "ledger_ids", "count": len(ledger_map)})

    # Fetch per-property tenant balances once
    _emit("fetch_start", {"step": "lease_balances"})
    balance_index = _fetch_lease_balances()
    _emit("fetch_end", {"step": "lease_balances", "count": len(balance_index)})

    fetch_stats = {
        "transactions": len(transactions),
        "ledger_ids": len(ledger_map),
        "properties_with_balance": len(balance_index),
    }

    # ET-aware "today" for undeposited_as_of
    today_et = timezone.localtime(timezone.now()).date()

    # Build snapshots
    created = 0
    updated = 0
    errors = []
    outcomes = []

    for idx, portfolio in enumerate(portfolio_list, 1):
        try:
            payload = build_distribution_snapshot(
                portfolio, month_start, month_end, transactions,
                balance_index=balance_index,
            )

            # Fetch undeposited balance for every active portfolio.
            # Once captured (undeposited_as_of is set), freeze — never
            # re-fetch so a later re-run cannot overwrite the original value.
            existing_snapshot = (
                PortfolioDistributionSnapshot.objects.filter(
                    portfolio=portfolio,
                    period_type="monthly",
                    period_start=month_start,
                )
                .values("undeposited_amount", "undeposited_as_of")
                .first()
            )

            if existing_snapshot and existing_snapshot["undeposited_as_of"] is not None:
                # Already captured — carry forward unchanged
                payload["undeposited_amount"] = existing_snapshot["undeposited_amount"]
                payload["undeposited_as_of"] = existing_snapshot["undeposited_as_of"]
                undeposited_source = "frozen"
            else:
                # First capture — fetch live from RentVine
                ledger_id = ledger_map.get(portfolio.rentvine_id)
                if ledger_id:
                    try:
                        balances = _fetch_portfolio_balances(ledger_id)
                        payload["undeposited_amount"] = get_undeposited_rent(balances)
                        payload["undeposited_as_of"] = today_et
                        undeposited_source = "fetched"
                    except Exception as bal_exc:
                        logger.warning(
                            "comms_distribution_balances_error",
                            extra={
                                "portfolio": str(portfolio),
                                "ledger_id": ledger_id,
                                "error": str(bal_exc),
                            },
                        )
                        undeposited_source = "error"
                        # Default to $0 — don't crash the batch
                else:
                    logger.warning(
                        "comms_distribution_missing_ledger",
                        extra={
                            "portfolio": str(portfolio),
                            "rentvine_id": portfolio.rentvine_id,
                        },
                    )
                    undeposited_source = "no-ledger"
                    # Default to $0 — undeposited stays at model default

            snapshot, was_created = upsert_distribution_snapshot(payload)

            label = "created" if was_created else "updated"
            if was_created:
                created += 1
            else:
                updated += 1

            prop_count = len(payload["line_items"])
            total_expected = sum(
                Decimal(str(item["expected"])) for item in payload["line_items"]
            )
            total_collected = sum(
                Decimal(str(item["collected"])) for item in payload["line_items"]
            )

            outcome = {
                "index": idx,
                "total": total,
                "portfolio_name": portfolio.name,
                "status": label,
                "properties": prop_count,
                "expected": total_expected,
                "collected": total_collected,
                "distribution_amount": payload["distribution_amount"],
                "distribution_date": payload["distribution_date"],
                "undeposited_amount": payload.get("undeposited_amount") or Decimal("0"),
                "undeposited_source": undeposited_source,
                "error_message": None,
            }
            outcomes.append(outcome)
            _emit("portfolio_done", outcome)

        except Exception as exc:
            msg = f"  [{idx}/{total}] {portfolio.name}: ERROR — {exc}"
            errors.append(msg)
            logger.exception(
                "comms_distribution_snapshot_error",
                extra={
                    "portfolio": str(portfolio),
                    "month": str(month_start),
                    "error": str(exc),
                },
            )
            outcome = {
                "index": idx,
                "total": total,
                "portfolio_name": portfolio.name,
                "status": "error",
                "properties": 0,
                "expected": Decimal("0"),
                "collected": Decimal("0"),
                "distribution_amount": Decimal("0"),
                "distribution_date": None,
                "undeposited_amount": Decimal("0"),
                "undeposited_source": "",
                "error_message": str(exc),
            }
            outcomes.append(outcome)
            _emit("portfolio_done", outcome)

    _emit("complete", {
        "created": created,
        "updated": updated,
        "errors": len(errors),
    })

    return {
        "created": created,
        "updated": updated,
        "errors": errors,
        "outcomes": outcomes,
        "fetch_stats": fetch_stats,
    }
