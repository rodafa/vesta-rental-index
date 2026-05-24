"""
Final verification: test both get_financial_summary() and
get_portfolio_financial_summary() logic against live RentVine API.

Usage: python test_rentvine_transactions.py
"""
import json
import requests
import sys
import os

sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None

SUBDOMAIN = "vestapm"
API_KEY = "00fbe0caa7b04e359ab6c965e1fe5ae9"
API_SECRET = "5e552e58f7e246f582fe0b0165b51a01"
BASE_URL = f"https://{SUBDOMAIN}.rentvine.com/api/manager"

session = requests.Session()
session.auth = (API_KEY, API_SECRET)
session.headers.update({"Accept": "application/json"})


def api_get(path, params=None):
    url = f"{BASE_URL}/{path.lstrip('/')}"
    resp = session.get(url, params=params, timeout=30)
    return resp.json()


def extract_transactions(response_data):
    if isinstance(response_data, list):
        return response_data
    if isinstance(response_data, dict):
        return response_data.get("data") or response_data.get("results") or []
    return []


def sum_charges_and_payments(transactions):
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
        if txn_type == 1:
            charged += amount
        elif txn_type == 2:
            paid += amount
    return charged, paid


TYPE_LABELS = {
    "1": "Lease Charge", "2": "Lease Payment", "3": "Lease Credit",
    "4": "Pmt Return", "5": "Owner Pmt", "7": "Bill Charge",
    "8": "Bill Payout", "14": "Mgmt Fee", "15": "Ledger Xfer",
}


# ============================================================
# TEST 1: get_financial_summary() for 23 Deep Woods Trail
# ============================================================
print("=" * 70)
print("TEST 1: get_financial_summary() -- 23 Deep Woods Trail (propertyID=4)")
print("        Period: April 2026")
print("=" * 70)

# Period query
resp = api_get("accounting/transactions/search", params={
    "propertyID": 4,
    "datePostedMin": "2026-04-01",
    "datePostedMax": "2026-04-30",
    "pageSize": 500,
})
txns = extract_transactions(resp)
charged, paid = sum_charges_and_payments(txns)
outstanding = max(charged - paid, 0)

print(f"\n  Period transactions: {len(txns)}")
for i, txn in enumerate(txns):
    t = txn.get("transaction", txn)
    tid = str(t.get("transactionTypeID", "?"))
    label = TYPE_LABELS.get(tid, f"Type {tid}")
    voided = " [VOIDED]" if str(t.get("isVoided", "0")) == "1" else ""
    print(f"    {label:18s} ${str(t.get('amount','')):>10s}  {t.get('datePosted')}  {str(t.get('description',''))[:40]}{voided}")

print(f"\n  RESULT: charged=${charged:,.2f}  paid=${paid:,.2f}  outstanding=${outstanding:,.2f}")

# All-time query
resp2 = api_get("accounting/transactions/search", params={
    "propertyID": 4,
    "pageSize": 1000,
})
txns2 = extract_transactions(resp2)
all_charged, all_paid = sum_charges_and_payments(txns2)
all_time_overdue = max(all_charged - all_paid, 0)
has_data = charged > 0 or paid > 0

print(f"\n  All-time ({len(txns2)} txns): charged=${all_charged:,.2f}  paid=${all_paid:,.2f}  overdue=${all_time_overdue:,.2f}")
print(f"  has_data={has_data}")


# ============================================================
# TEST 2: get_portfolio_financial_summary() -- Breaux Portfolio
#         Uses pagination + client-side portfolio filter
# ============================================================
print("\n" + "=" * 70)
print("TEST 2: get_portfolio_financial_summary() -- Breaux Portfolio (portfolioID=4)")
print("        Period: April 2026  (client-side filtering)")
print("=" * 70)

target_id = "4"
total = 0.0
page = 1
page_size = 500
all_matched = []

while True:
    resp3 = api_get("accounting/transactions/search", params={
        "transactionTypeID": 2,
        "datePostedMin": "2026-04-01",
        "datePostedMax": "2026-04-30",
        "page": page,
        "pageSize": page_size,
    })
    txns3 = extract_transactions(resp3)
    if not txns3:
        break
    print(f"  Page {page}: {len(txns3)} transactions fetched")

    for txn in txns3:
        port_obj = txn.get("portfolio") or {}
        if str(port_obj.get("portfolioID")) != target_id:
            continue
        t = txn.get("transaction", txn) if isinstance(txn, dict) else txn
        if str(t.get("isVoided", "0")) in ("1", "true", "True", True):
            continue
        try:
            amt = float(t.get("amount", 0))
        except (ValueError, TypeError):
            continue
        total += amt
        prop_addr = (txn.get("property") or {}).get("address", "?")
        all_matched.append({"amount": amt, "date": t.get("datePosted"), "address": prop_addr})

    if len(txns3) < page_size:
        break
    page += 1

print(f"\n  Matched transactions for Breaux Portfolio:")
for m in all_matched:
    print(f"    ${m['amount']:>10,.2f}  {m['date']}  {m['address']}")

print(f"\n  RESULT: total_received=${total:,.2f}")
print(f"  (reserve_amount, additional_reserve_amount, hold_distributions read from local Portfolio model)")


# ============================================================
# TEST 3: Cross-verify with a second portfolio (McMaster)
# ============================================================
print("\n" + "=" * 70)
print("TEST 3: Cross-verify -- McMaster Holdings LLC (portfolioID=6)")
print("        Period: April 2026")
print("=" * 70)

target_id2 = "6"
total2 = 0.0
page = 1
matched2 = []

while True:
    resp4 = api_get("accounting/transactions/search", params={
        "transactionTypeID": 2,
        "datePostedMin": "2026-04-01",
        "datePostedMax": "2026-04-30",
        "page": page,
        "pageSize": page_size,
    })
    txns4 = extract_transactions(resp4)
    if not txns4:
        break

    for txn in txns4:
        port_obj = txn.get("portfolio") or {}
        if str(port_obj.get("portfolioID")) != target_id2:
            continue
        t = txn.get("transaction", txn) if isinstance(txn, dict) else txn
        if str(t.get("isVoided", "0")) in ("1", "true", "True", True):
            continue
        try:
            amt = float(t.get("amount", 0))
        except (ValueError, TypeError):
            continue
        total2 += amt
        prop_addr = (txn.get("property") or {}).get("address", "?")
        matched2.append({"amount": amt, "date": t.get("datePosted"), "address": prop_addr})

    if len(txns4) < page_size:
        break
    page += 1

print(f"\n  Matched transactions for McMaster Holdings:")
for m in matched2:
    print(f"    ${m['amount']:>10,.2f}  {m['date']}  {m['address']}")

print(f"\n  RESULT: total_received=${total2:,.2f}")


# ============================================================
# SUMMARY
# ============================================================
print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print("""
  get_financial_summary() [property-level]:
    - propertyID filter: WORKS
    - datePostedMin/Max: WORKS
    - Data for 23 Deep Woods Trail, Apr 2026:
      charged (type 1): ${0:,.2f}
      paid (type 2):    ${1:,.2f}
      outstanding:      ${2:,.2f}
      all-time overdue: ${3:,.2f}

  get_portfolio_financial_summary() [portfolio-level]:
    - portfolioID filter: BROKEN (returns all portfolios)
    - Fix: paginate + client-side filter by portfolio.portfolioID
    - Breaux Portfolio total received: ${4:,.2f}
    - McMaster Holdings total received: ${5:,.2f}

  VERIFY these numbers in the RentVine UI before deploying.
""".format(charged, paid, outstanding, all_time_overdue, total, total2))
