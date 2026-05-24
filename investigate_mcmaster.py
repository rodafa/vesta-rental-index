"""
Investigate McMaster Holdings (portfolioID=6) transactions for April 2026.
Find where the $1,681.75 discrepancy lives between API type-2 sum ($11,884.92)
and owner statement Total Income ($13,566.67).
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import requests
from collections import defaultdict
from decimal import Decimal

# --- Config ---
SUBDOMAIN = "vestapm"
API_KEY = "00fbe0caa7b04e359ab6c965e1fe5ae9"
API_SECRET = "5e552e58f7e246f582fe0b0165b51a01"
BASE_URL = f"https://{SUBDOMAIN}.rentvine.com/api/manager"

session = requests.Session()
session.auth = (API_KEY, API_SECRET)
session.headers.update({"Accept": "application/json"})

TRANSACTION_TYPE_LABELS = {
    1: "Lease Charge",
    2: "Lease Payment",
    3: "Lease Credit",
    4: "Pmt Return",
    5: "Owner Pmt",
    7: "Bill Charge",
    8: "Bill Payout",
    14: "Mgmt Fee",
    15: "Ledger Xfer",
    16: "Ledger Payout",
    19: "Deposit Release",
    20: "Lease Payout",
}

# Properties where payments are known missing
MISSING_PROPERTIES = [
    "130 Reems Creek Road",
    "61 Scates Street",
    "2 3rd Street",
]

PORTFOLIO_ID = "6"


def api_get(path, params=None):
    url = f"{BASE_URL}/{path.lstrip('/')}"
    resp = session.get(url, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def extract_transactions(response_data):
    if isinstance(response_data, list):
        return response_data
    if isinstance(response_data, dict):
        return response_data.get("data") or response_data.get("results") or []
    return []


def fetch_all_april_transactions():
    """Fetch ALL transaction types for April 2026, paginating."""
    all_items = []
    page = 1
    page_size = 500

    while True:
        print(f"  Fetching page {page}...")
        resp = api_get("accounting/transactions/search", params={
            "datePostedMin": "2026-04-01",
            "datePostedMax": "2026-04-30",
            "page": page,
            "pageSize": page_size,
        })
        txns = extract_transactions(resp)
        print(f"    Got {len(txns)} items")
        if not txns:
            break
        all_items.extend(txns)
        if len(txns) < page_size:
            break
        page += 1

    return all_items


def filter_mcmaster(items):
    """Filter to only McMaster Holdings (portfolioID=6)."""
    result = []
    for item in items:
        portfolio = item.get("portfolio") or {}
        pid = str(portfolio.get("portfolioID", ""))
        if pid == PORTFOLIO_ID:
            result.append(item)
    return result


def is_missing_property(address):
    """Check if this address matches one of the known missing-payment properties."""
    if not address:
        return False
    addr_lower = address.lower()
    for mp in MISSING_PROPERTIES:
        if mp.lower() in addr_lower:
            return True
    return False


def main():
    print("=" * 80)
    print("McMaster Holdings (portfolioID=6) - April 2026 Transaction Investigation")
    print("=" * 80)

    # 1. Fetch all transactions for April
    print("\n[1] Fetching all April 2026 transactions (all types)...")
    all_items = fetch_all_april_transactions()
    print(f"    Total transactions fetched: {len(all_items)}")

    # 2. Filter to McMaster
    print("\n[2] Filtering to McMaster Holdings (portfolioID=6)...")
    mcmaster = filter_mcmaster(all_items)
    print(f"    McMaster transactions: {len(mcmaster)}")

    if not mcmaster:
        print("    ERROR: No McMaster transactions found.")
        return

    # 3. Parse all McMaster transactions
    by_type = defaultdict(list)
    all_parsed = []

    for item in mcmaster:
        txn = item.get("transaction") or {}
        prop = item.get("property") or {}
        unit = item.get("unit") or {}

        type_id = txn.get("transactionTypeID")
        try:
            type_id_int = int(type_id) if type_id is not None else None
        except (ValueError, TypeError):
            type_id_int = None

        amount = Decimal(str(txn.get("amount", 0)))
        date_posted = txn.get("datePosted", "N/A")
        description = txn.get("description", "N/A")
        prop_address = prop.get("address", "N/A")
        unit_name = unit.get("name", "N/A") if unit else "N/A"
        unit_address = unit.get("address", "N/A") if unit else "N/A"
        is_voided = str(txn.get("isVoided", "0")) in ("1", "true", "True")
        txn_id = txn.get("transactionID", "N/A")

        record = {
            "transactionID": txn_id,
            "transactionTypeID": type_id_int,
            "amount": amount,
            "datePosted": date_posted,
            "description": description,
            "propertyAddress": prop_address,
            "unitName": unit_name,
            "unitAddress": unit_address,
            "isVoided": is_voided,
        }
        all_parsed.append(record)
        by_type[type_id_int].append(record)

    # 4. Print ALL transactions grouped by type
    print("\n" + "=" * 80)
    print("[3] ALL McMaster April 2026 Transactions by Type")
    print("=" * 80)

    grand_total = Decimal("0")

    for type_id in sorted(by_type.keys(), key=lambda x: x if x is not None else 999):
        records = by_type[type_id]
        label = TRANSACTION_TYPE_LABELS.get(type_id, f"Unknown({type_id})")
        subtotal = sum(r["amount"] for r in records)
        non_voided_subtotal = sum(r["amount"] for r in records if not r["isVoided"])
        grand_total += subtotal

        voided_count = sum(1 for r in records if r["isVoided"])
        voided_note = f" ({voided_count} voided)" if voided_count else ""

        print(f"\n--- Type {type_id}: {label} ({len(records)} txns{voided_note}, subtotal: ${subtotal:,.2f}, non-voided: ${non_voided_subtotal:,.2f}) ---")
        print(f"  {'Date':<12} {'Amount':>12} {'Voided':<8} {'Property':<30} {'Unit':<6} {'Description'}")
        print(f"  {'-'*12} {'-'*12} {'-'*8} {'-'*30} {'-'*6} {'-'*40}")

        for r in sorted(records, key=lambda x: (x["datePosted"], x["propertyAddress"])):
            voided_str = "YES" if r["isVoided"] else ""
            print(
                f"  {r['datePosted']:<12} {r['amount']:>12,.2f} {voided_str:<8} "
                f"{str(r['propertyAddress']):<30} {str(r['unitName']):<6} "
                f"{str(r['description'])[:50]}"
            )

    print(f"\n  GRAND TOTAL (all types, incl voided): ${grand_total:,.2f}")

    # 5. Summary table
    print("\n" + "=" * 80)
    print("[4] Type Summary (non-voided only)")
    print("=" * 80)
    print(f"  {'TypeID':<8} {'Label':<20} {'Count':>6} {'Subtotal':>14}")
    print(f"  {'-'*8} {'-'*20} {'-'*6} {'-'*14}")

    nv_grand = Decimal("0")
    for type_id in sorted(by_type.keys(), key=lambda x: x if x is not None else 999):
        records = [r for r in by_type[type_id] if not r["isVoided"]]
        label = TRANSACTION_TYPE_LABELS.get(type_id, f"Unknown({type_id})")
        subtotal = sum(r["amount"] for r in records)
        nv_grand += subtotal
        print(f"  {str(type_id):<8} {label:<20} {len(records):>6} ${subtotal:>12,.2f}")

    print(f"  {'':8} {'TOTAL':<20} {'':>6} ${nv_grand:>12,.2f}")

    # 6. Focus on missing-payment properties
    print("\n" + "=" * 80)
    print("[5] Transactions for MISSING-PAYMENT Properties")
    print("    (130 Reems Creek Road, 61 Scates Street, 2 3rd Street)")
    print("=" * 80)

    missing_txns = [r for r in all_parsed if is_missing_property(r["propertyAddress"])]

    if not missing_txns:
        print("  NO transactions found for these properties at all!")
    else:
        print(f"  Found {len(missing_txns)} transactions:\n")
        print(
            f"  {'TxnID':<10} {'TypeID':<8} {'Type Label':<16} {'Date':<12} "
            f"{'Amount':>12} {'Voided':<8} {'Property':<28} {'Unit':<6} {'Description'}"
        )
        print(
            f"  {'-'*10} {'-'*8} {'-'*16} {'-'*12} "
            f"{'-'*12} {'-'*8} {'-'*28} {'-'*6} {'-'*30}"
        )

        for r in sorted(missing_txns, key=lambda x: (str(x["propertyAddress"]), str(x["unitName"]), x["transactionTypeID"] or 0)):
            label = TRANSACTION_TYPE_LABELS.get(r["transactionTypeID"], f"Type {r['transactionTypeID']}")
            voided_str = "YES" if r["isVoided"] else ""
            print(
                f"  {str(r['transactionID']):<10} {str(r['transactionTypeID']):<8} {label:<16} "
                f"{r['datePosted']:<12} {r['amount']:>12,.2f} {voided_str:<8} "
                f"{str(r['propertyAddress']):<28} {str(r['unitName']):<6} "
                f"{str(r['description'])[:40]}"
            )

        # Sub-summary for missing properties by type
        missing_by_type = defaultdict(lambda: {"count": 0, "total": Decimal("0")})
        for r in missing_txns:
            if not r["isVoided"]:
                key = r["transactionTypeID"]
                missing_by_type[key]["count"] += 1
                missing_by_type[key]["total"] += r["amount"]

        missing_total = sum(v["total"] for v in missing_by_type.values())
        print(f"\n  Summary for missing-payment properties (non-voided):")
        print(f"  {'TypeID':<8} {'Label':<20} {'Count':>6} {'Subtotal':>14}")
        print(f"  {'-'*8} {'-'*20} {'-'*6} {'-'*14}")
        for type_id in sorted(missing_by_type.keys(), key=lambda x: x if x is not None else 999):
            label = TRANSACTION_TYPE_LABELS.get(type_id, f"Type {type_id}")
            info = missing_by_type[type_id]
            print(f"  {str(type_id):<8} {label:<20} {info['count']:>6} ${info['total']:>12,.2f}")
        print(f"  {'':8} {'TOTAL':<20} {'':>6} ${missing_total:>12,.2f}")

    # 7. Reconciliation
    print("\n" + "=" * 80)
    print("[6] Reconciliation")
    print("=" * 80)

    # Non-voided sums
    type2_nv = sum(r["amount"] for r in by_type.get(2, []) if not r["isVoided"])
    type1_nv = sum(r["amount"] for r in by_type.get(1, []) if not r["isVoided"])
    type3_nv = sum(r["amount"] for r in by_type.get(3, []) if not r["isVoided"])

    stmt_income = Decimal("13566.67")
    stmt_rent = Decimal("13266.67")
    stmt_pet = Decimal("100.00")
    stmt_misc = Decimal("300.00")
    stmt_credit = Decimal("-100.00")

    print(f"  Owner Statement Breakdown:")
    print(f"    Rent Income:           ${stmt_rent:>12,.2f}")
    print(f"    Pet Rent:              ${stmt_pet:>12,.2f}")
    print(f"    Misc. Tenant Charge:   ${stmt_misc:>12,.2f}")
    print(f"    Tenant Credit:         ${stmt_credit:>12,.2f}")
    print(f"    -------------------------------------------")
    print(f"    TOTAL INCOME:          ${stmt_income:>12,.2f}")

    print()
    print(f"  API Results (non-voided):")
    print(f"    Type 1 (Lease Charge): ${type1_nv:>12,.2f}")
    print(f"    Type 2 (Lease Payment):${type2_nv:>12,.2f}")
    print(f"    Type 3 (Lease Credit): ${type3_nv:>12,.2f}")
    print()
    print(f"  Comparisons:")
    print(f"    Statement Total Income:        ${stmt_income:>12,.2f}")
    print(f"    API Type 2 only:               ${type2_nv:>12,.2f}")
    print(f"    Gap (type 2 vs statement):     ${stmt_income - type2_nv:>12,.2f}")
    print()
    print(f"    API Type 2 + Type 3:           ${type2_nv + type3_nv:>12,.2f}")
    print(f"    Gap (type 2+3 vs statement):   ${stmt_income - (type2_nv + type3_nv):>12,.2f}")
    print()
    print(f"    API Type 1 + 2 + 3:            ${type1_nv + type2_nv + type3_nv:>12,.2f}")
    print(f"    Gap (1+2+3 vs statement):      ${stmt_income - (type1_nv + type2_nv + type3_nv):>12,.2f}")

    # Check if Pet Rent and Misc Tenant Charge might be different types
    print()
    print("  Checking if $100 (pet rent) and $300 (misc charge) appear under any type...")
    for r in all_parsed:
        if r["isVoided"]:
            continue
        if r["amount"] == Decimal("100.00") or r["amount"] == Decimal("300.00"):
            label = TRANSACTION_TYPE_LABELS.get(r["transactionTypeID"], f"Type {r['transactionTypeID']}")
            print(f"    -> ${r['amount']:,.2f} | Type {r['transactionTypeID']} ({label}) | {r['propertyAddress']} unit {r['unitName']} | {r['description']}")

    print("\n" + "=" * 80)
    print("Investigation complete.")
    print("=" * 80)


if __name__ == "__main__":
    main()
