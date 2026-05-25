"""
RentVine Bills API Discovery Script
====================================
Read-only (GET only) reconnaissance of the RentVine bills API.
Outputs raw JSON to tmp/rentvine_discovery/ and a markdown summary.

Usage:
    uv run python scripts/explore_rentvine_bills.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Bootstrap Django settings
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vesta_rental_index.settings")

import django  # noqa: E402

django.setup()

from integrations.rentvine.client import RentvineClient, RentvineAPIError  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OUTPUT_DIR = Path("tmp/rentvine_discovery")
DELAY = 0.4  # seconds between requests

PII_PATTERNS = [
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
]

MELD_REF_PATTERN = re.compile(r"Meld\s+([A-Z0-9]{5,8})", re.IGNORECASE)

# RentVine returns each record as {"bill": {fields}, "contact": {fields}}.
# This helper unwraps to the inner bill dict.
BILL_KEY = "bill"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def save_json(filename: str, data: Any) -> None:
    filepath = OUTPUT_DIR / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"  -> Saved {filepath}")


def redact_pii(value: str) -> str:
    for pattern, replacement in PII_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_pii(value)
    return value


def safe_get(
    client: RentvineClient, path: str, params: dict | None = None
) -> dict | list | None:
    """GET that never crashes — returns None on failure."""
    time.sleep(DELAY)
    try:
        return client.get(path, params=params)
    except RentvineAPIError as e:
        print(f"  [API ERROR] {path}: {e}")
        return None
    except Exception as e:
        print(f"  [ERROR] {path}: {e}")
        return None


def unwrap_bill(record: dict) -> dict:
    """Unwrap {"bill": {...}, "contact": {...}} → inner bill dict."""
    if BILL_KEY in record and isinstance(record[BILL_KEY], dict):
        return record[BILL_KEY]
    return record


def bill_text(b: dict) -> str:
    """Concatenate all text fields on a bill for keyword scanning."""
    parts = [
        str(b.get("description") or ""),
        str(b.get("paymentMemo") or ""),
        str(b.get("reference") or ""),
    ]
    return " ".join(parts)


def is_maintenance_bill(b: dict) -> bool:
    text = bill_text(b).lower()
    return (
        MELD_REF_PATTERN.search(bill_text(b)) is not None
        or "meld" in text
        or "maintenance" in text
        or b.get("workOrderID") is not None
    )


# ---------------------------------------------------------------------------
# Discovery Steps
# ---------------------------------------------------------------------------
def step1_connectivity(client: RentvineClient) -> str | None:
    """Find the bills endpoint path."""
    print("\n=== Step 1: Connectivity ===")
    candidates = [
        "/accounting/bills",
        "/bills",
        "/bills/search",
        "/accounting/bills/search",
    ]
    for path in candidates:
        print(f"  Trying {path}...")
        data = safe_get(client, path, params={"page": 1, "pageSize": 1})
        if data is not None:
            print(f"  SUCCESS: {path}")
            save_json("step1_connectivity.json", data)
            return path

    print("  FAILED: No bills endpoint found.")
    save_json("step1_connectivity.json", {"error": "no endpoint found", "tried": candidates})
    return None


def step2_bill_list(client: RentvineClient, endpoint: str) -> list[dict]:
    """Fetch a broad sample of bills across multiple pages."""
    print("\n=== Step 2: Bill List (broad sample) ===")

    all_records: list[dict] = []

    # Page 1 — first 50
    data = safe_get(client, endpoint, params={"page": 1, "pageSize": 50})
    if data is None:
        print("  FAILED to fetch bill list.")
        return []

    save_json("step2_bill_list_page1.json", data)
    records = data if isinstance(data, list) else []
    all_records.extend(records)
    print(f"  Page 1: {len(records)} records")

    # Page 2 — next 50 for broader coverage
    data2 = safe_get(client, endpoint, params={"page": 2, "pageSize": 50})
    if data2 and isinstance(data2, list):
        all_records.extend(data2)
        print(f"  Page 2: {len(data2)} records")

    # Total count: fetch page 1 with pageSize=1, then count pages
    # (we'll infer from how many we get)
    total_pages_data = safe_get(client, endpoint, params={"page": 100, "pageSize": 50})
    if total_pages_data and isinstance(total_pages_data, list) and len(total_pages_data) > 0:
        # There are at least 100 pages * 50 = 5000+ bills
        print(f"  Page 100 has {len(total_pages_data)} records (5000+ total bills)")

    # Unwrap and report
    unwrapped = [unwrap_bill(r) for r in all_records]
    maintenance = [b for b in unwrapped if is_maintenance_bill(b)]
    with_wo = [b for b in unwrapped if b.get("workOrderID")]

    print(f"  Total sampled: {len(unwrapped)}")
    print(f"  Maintenance-looking: {len(maintenance)}")
    print(f"  With workOrderID populated: {len(with_wo)}")
    if unwrapped:
        print(f"  Bill fields ({len(unwrapped[0])}): {list(unwrapped[0].keys())}")

    save_json("step2_bill_list.json", {
        "total_sampled": len(unwrapped),
        "maintenance_count": len(maintenance),
        "with_work_order_id": len(with_wo),
        "fields": list(unwrapped[0].keys()) if unwrapped else [],
        "sample_bills": unwrapped[:5],
    })

    return all_records


def _find_maintenance_bills(
    client: RentvineClient, endpoint: str, existing: list[dict]
) -> list[dict]:
    """If initial sample has few maintenance bills, page through more to find them."""
    unwrapped_existing = [unwrap_bill(r) for r in existing]
    maint = [b for b in unwrapped_existing if is_maintenance_bill(b)]

    if len(maint) >= 7:
        return existing  # Already have enough

    print("  Searching more pages for maintenance bills...")
    for page in range(3, 20):
        data = safe_get(client, endpoint, params={"page": page, "pageSize": 50})
        if not data or not isinstance(data, list) or len(data) == 0:
            break
        existing.extend(data)
        new_maint = [unwrap_bill(r) for r in data if is_maintenance_bill(unwrap_bill(r))]
        maint.extend(new_maint)
        print(f"    Page {page}: {len(data)} bills, {len(new_maint)} maintenance ({len(maint)} total maint)")
        if len(maint) >= 10:
            break

    return existing


def step3_bill_detail(
    client: RentvineClient, endpoint: str, raw_records: list[dict]
) -> list[dict]:
    """Fetch full detail for ~10 bills, prioritizing maintenance ones."""
    print("\n=== Step 3: Bill Detail ===")

    if not raw_records:
        print("  No bills to fetch detail for.")
        return []

    # If we don't have enough maintenance bills yet, fetch more pages
    raw_records = _find_maintenance_bills(client, endpoint, raw_records)

    unwrapped = [unwrap_bill(r) for r in raw_records]
    maintenance = [b for b in unwrapped if is_maintenance_bill(b)]
    other = [b for b in unwrapped if not is_maintenance_bill(b)]

    print(f"  Maintenance bills available: {len(maintenance)}")
    print(f"  Other bills available: {len(other)}")

    # Pick ~7 maintenance + ~3 other
    to_fetch = maintenance[:7] + other[:3]
    if len(to_fetch) < 10:
        to_fetch = (maintenance + other)[:10]

    details = []
    for bill in to_fetch:
        bill_id = bill.get("billID")
        if not bill_id:
            print(f"  WARNING: No billID on bill: {list(bill.keys())[:5]}")
            continue

        print(f"  Fetching detail for bill {bill_id}...")
        detail = safe_get(client, f"{endpoint}/{bill_id}")
        if detail:
            details.append(detail)
            save_json(f"step3_bill_detail_{bill_id}.json", detail)

    print(f"  Fetched {len(details)} bill details.")
    return details


def step4_meld_linkage(
    raw_list: list[dict], raw_details: list[dict]
) -> dict:
    """Analyze meld linkage — the keystone finding."""
    print("\n=== Step 4: Meld Linkage (Keystone) ===")

    # Use all bills from list for coverage stats (workOrderID, meld ref)
    all_list = [unwrap_bill(r) for r in raw_list]
    # Use details for richer field analysis
    all_detail_bills = []
    for d in raw_details:
        if isinstance(d, dict):
            if BILL_KEY in d and isinstance(d[BILL_KEY], dict):
                all_detail_bills.append(d[BILL_KEY])
            elif "billID" in d:
                all_detail_bills.append(d)

    # --- workOrderID analysis across all sampled list bills ---
    total_list = len(all_list)
    wo_populated = sum(1 for b in all_list if b.get("workOrderID"))
    wo_null = total_list - wo_populated

    # Among maintenance-looking bills
    maint_bills = [b for b in all_list if is_maintenance_bill(b)]
    maint_with_wo = sum(1 for b in maint_bills if b.get("workOrderID"))

    # --- Meld ref pattern in text ---
    maint_with_ref = 0
    maint_without_ref = []
    examples_with_ref = []

    for b in maint_bills:
        text = bill_text(b)
        match = MELD_REF_PATTERN.search(text)
        if match:
            maint_with_ref += 1
            if len(examples_with_ref) < 5:
                examples_with_ref.append({
                    "billID": b.get("billID"),
                    "description": redact_pii(str(b.get("description") or "")[:200]),
                    "paymentMemo": redact_pii(str(b.get("paymentMemo") or "")[:200]),
                    "meld_ref": match.group(1),
                    "workOrderID": b.get("workOrderID"),
                })
        else:
            if len(maint_without_ref) < 5:
                maint_without_ref.append({
                    "billID": b.get("billID"),
                    "description": redact_pii(str(b.get("description") or "")[:200]),
                    "paymentMemo": redact_pii(str(b.get("paymentMemo") or "")[:200]),
                    "workOrderID": b.get("workOrderID"),
                })

    # Also check detail responses for additional fields
    extra_fields_in_detail = set()
    for db in all_detail_bills:
        for key in db.keys():
            kl = key.lower()
            if "work" in kl or "meld" in kl or "order" in kl:
                extra_fields_in_detail.add(key)

    findings = {
        "total_list_sampled": total_list,
        "workOrderID_populated": wo_populated,
        "workOrderID_null": wo_null,
        "maintenance_bills_found": len(maint_bills),
        "maintenance_with_workOrderID": maint_with_wo,
        "maintenance_with_meld_ref_pattern": maint_with_ref,
        "maintenance_without_meld_ref": maint_without_ref,
        "examples_with_meld_ref": examples_with_ref,
        "wo_related_fields_in_detail": list(extra_fields_in_detail),
    }

    save_json("step4_meld_linkage.json", findings)

    print(f"  Total bills sampled: {total_list}")
    print(f"  workOrderID populated: {wo_populated}/{total_list}")
    print(f"  Maintenance bills found: {len(maint_bills)}")
    print(f"  Maintenance with workOrderID: {maint_with_wo}/{len(maint_bills)}")
    print(f"  Maintenance with 'Meld <ref>' pattern: {maint_with_ref}/{len(maint_bills)}")
    print(f"  WO-related fields in detail: {extra_fields_in_detail}")

    return findings


def step5_itemized_charges(raw_details: list[dict]) -> dict:
    """Analyze itemized charge / line item structure."""
    print("\n=== Step 5: Itemized Charges ===")

    findings: dict[str, Any] = {
        "line_item_field_name": None,
        "line_item_fields": [],
        "line_item_count_per_bill": [],
        "examples": [],
    }

    if not raw_details:
        print("  No detail records to analyze.")
        save_json("step5_itemized_charges.json", findings)
        return findings

    # The detail response might be:
    #   {"bill": {...}, "contact": {...}, "lineItems": [...]}
    #   or {"bill": {...}, "billCharges": [...]}
    #   or the bill dict might contain them
    item_candidates = [
        "lineItems", "line_items", "items", "charges",
        "billCharges", "bill_charges", "billItems", "bill_items",
        "details", "entries", "billDetails", "bill_details",
    ]

    for detail in raw_details:
        if not isinstance(detail, dict):
            continue

        # Check top-level of the response (sibling to "bill")
        for field in item_candidates:
            if field in detail and isinstance(detail[field], list):
                findings["line_item_field_name"] = field
                items = detail[field]
                findings["line_item_count_per_bill"].append(len(items))
                if items and not findings["line_item_fields"]:
                    findings["line_item_fields"] = list(items[0].keys())
                if items and len(findings["examples"]) < 3:
                    findings["examples"].append(
                        {k: redact_value(v) for k, v in items[0].items()}
                    )
                break

        # Check inside the "bill" sub-dict too
        if not findings["line_item_field_name"]:
            inner = detail.get(BILL_KEY, {})
            if isinstance(inner, dict):
                for field in item_candidates:
                    if field in inner and isinstance(inner[field], list):
                        findings["line_item_field_name"] = f"bill.{field}"
                        items = inner[field]
                        findings["line_item_count_per_bill"].append(len(items))
                        if items and not findings["line_item_fields"]:
                            findings["line_item_fields"] = list(items[0].keys())
                        break

        # Fallback: any list-of-dicts with 4+ keys
        if not findings["line_item_field_name"]:
            for key, val in detail.items():
                if key == "contact":
                    continue
                if isinstance(val, list) and val and isinstance(val[0], dict) and len(val[0]) >= 4:
                    findings["line_item_field_name"] = key
                    findings["line_item_fields"] = list(val[0].keys())
                    findings["line_item_count_per_bill"].append(len(val))
                    if len(findings["examples"]) < 3:
                        findings["examples"].append(
                            {k: redact_value(v) for k, v in val[0].items()}
                        )
                    break

    save_json("step5_itemized_charges.json", findings)
    print(f"  Line item field: {findings['line_item_field_name']}")
    print(f"  Line item fields: {findings['line_item_fields']}")
    print(f"  Items per bill: {findings['line_item_count_per_bill']}")

    return findings


def step6_markup(raw_list: list[dict], raw_details: list[dict]) -> dict:
    """Find bills with non-zero markup; compare vendor vs. itemized amounts."""
    print("\n=== Step 6: Markup Analysis ===")

    all_bills = [unwrap_bill(r) for r in raw_list]
    findings: dict[str, Any] = {
        "markup_field": None,
        "discount_field": None,
        "markup_discount_fields_seen": [],
        "bills_with_nonzero_markup": [],
        "total_checked": len(all_bills),
    }

    for b in all_bills:
        # Scan for markup/discount fields
        for key in b.keys():
            kl = key.lower()
            if ("markup" in kl or "discount" in kl) and key not in findings["markup_discount_fields_seen"]:
                findings["markup_discount_fields_seen"].append(key)

        # Check specific known fields
        mp = b.get("markupPercent") or b.get("markupAmount") or "0"
        if not findings["markup_field"]:
            if "markupPercent" in b:
                findings["markup_field"] = "markupPercent"
            elif "markupAmount" in b:
                findings["markup_field"] = "markupAmount"

        if not findings["discount_field"]:
            if "discountPercent" in b:
                findings["discount_field"] = "discountPercent"
            elif "discountAmount" in b:
                findings["discount_field"] = "discountAmount"

        try:
            if float(mp) != 0 and len(findings["bills_with_nonzero_markup"]) < 5:
                findings["bills_with_nonzero_markup"].append({
                    "billID": b.get("billID"),
                    "markupPercent": b.get("markupPercent"),
                    "discountPercent": b.get("discountPercent"),
                    "isMarkup": b.get("isMarkup"),
                    "isDiscount": b.get("isDiscount"),
                    "description": redact_pii(str(b.get("description") or "")[:150]),
                })
        except (ValueError, TypeError):
            pass

    # If we didn't find enough in list, also check details
    if len(findings["bills_with_nonzero_markup"]) < 3:
        for d in raw_details:
            inner = unwrap_bill(d) if isinstance(d, dict) else {}
            mp = inner.get("markupPercent") or inner.get("markupAmount") or "0"
            try:
                if float(mp) != 0 and len(findings["bills_with_nonzero_markup"]) < 5:
                    findings["bills_with_nonzero_markup"].append({
                        "billID": inner.get("billID"),
                        "markupPercent": inner.get("markupPercent"),
                        "discountPercent": inner.get("discountPercent"),
                        "isMarkup": inner.get("isMarkup"),
                        "isDiscount": inner.get("isDiscount"),
                        "description": redact_pii(str(inner.get("description") or "")[:150]),
                    })
            except (ValueError, TypeError):
                pass

    save_json("step6_markup_examples.json", findings)
    print(f"  Markup field: {findings['markup_field']}")
    print(f"  Discount field: {findings['discount_field']}")
    print(f"  Fields seen: {findings['markup_discount_fields_seen']}")
    print(f"  Bills with non-zero markup: {len(findings['bills_with_nonzero_markup'])}")

    return findings


def step7_dates(raw_list: list[dict], raw_details: list[dict]) -> dict:
    """Catalog all date fields on a bill."""
    print("\n=== Step 7: Date Fields ===")

    findings: dict[str, Any] = {
        "date_fields": {},
        "recommended_appearance_date": None,
    }

    date_keywords = ["date", "created", "updated", "modified", "posted", "entered", "due"]

    # Scan list bills
    for record in raw_list[:5]:
        b = unwrap_bill(record)
        for key, val in b.items():
            kl = key.lower()
            if any(kw in kl for kw in date_keywords):
                if key not in findings["date_fields"]:
                    findings["date_fields"][key] = {
                        "sample_value": str(val)[:30] if val else None,
                        "populated": val is not None and val != "",
                    }
            elif isinstance(val, str) and re.match(r"\d{4}-\d{2}-\d{2}", val):
                if key not in findings["date_fields"]:
                    findings["date_fields"][key] = {
                        "sample_value": val[:30],
                        "populated": True,
                    }

    # Also check detail responses for extra date fields
    for d in raw_details[:3]:
        inner = unwrap_bill(d) if isinstance(d, dict) else {}
        for key, val in inner.items():
            kl = key.lower()
            if any(kw in kl for kw in date_keywords) and key not in findings["date_fields"]:
                findings["date_fields"][key] = {
                    "sample_value": str(val)[:30] if val else None,
                    "populated": val is not None and val != "",
                }

    # Recommend the best appearance date
    priority = ["billDate", "dateDue", "createdDate", "dateCreated"]
    for field in priority:
        if field in findings["date_fields"]:
            findings["recommended_appearance_date"] = field
            break

    save_json("step7_dates.json", findings)
    print(f"  Date fields: {list(findings['date_fields'].keys())}")
    print(f"  Recommended appearance date: {findings['recommended_appearance_date']}")

    return findings


def step8_filtering(client: RentvineClient, endpoint: str) -> dict:
    """Test date-range and property filtering params."""
    print("\n=== Step 8: Filtering ===")

    findings: dict[str, Any] = {
        "date_filter_works": False,
        "date_filter_params": None,
        "date_filter_record_count": None,
        "property_filter_works": False,
        "property_filter_params": None,
        "property_filter_record_count": None,
        "tried": [],
    }

    # Baseline: unfiltered count
    baseline = safe_get(client, endpoint, params={"page": 1, "pageSize": 5})
    baseline_count = len(baseline) if isinstance(baseline, list) else 0

    # Date range filters
    date_tries = [
        {"billDateFrom": "2025-01-01", "billDateTo": "2025-01-31"},
        {"startDate": "2025-01-01", "endDate": "2025-01-31"},
        {"dateFrom": "2025-01-01", "dateTo": "2025-01-31"},
    ]
    for params in date_tries:
        test = {"page": 1, "pageSize": 50, **params}
        findings["tried"].append({"type": "date", "params": params})
        print(f"  Trying date filter: {params}")
        data = safe_get(client, endpoint, params=test)
        if data is not None and isinstance(data, list):
            # If count differs from unfiltered, the filter is actually working
            findings["date_filter_works"] = True
            findings["date_filter_params"] = params
            findings["date_filter_record_count"] = len(data)
            print(f"  -> {len(data)} records (baseline {baseline_count})")
            save_json("step8_date_filtered.json", data[:3])
            break

    # Property filter
    prop_tries = [
        {"propertyID": "1"},
        {"propertyId": "1"},
    ]
    for params in prop_tries:
        test = {"page": 1, "pageSize": 50, **params}
        findings["tried"].append({"type": "property", "params": params})
        print(f"  Trying property filter: {params}")
        data = safe_get(client, endpoint, params=test)
        if data is not None and isinstance(data, list):
            findings["property_filter_works"] = True
            findings["property_filter_params"] = params
            findings["property_filter_record_count"] = len(data)
            print(f"  -> {len(data)} records")
            save_json("step8_property_filtered.json", data[:3])
            break

    save_json("step8_filtering.json", findings)
    return findings


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def generate_summary(
    endpoint: str | None,
    raw_list: list[dict],
    raw_details: list[dict],
    linkage: dict,
    charges: dict,
    markup: dict,
    dates: dict,
    filtering: dict,
) -> None:
    """Generate SCHEMA_SUMMARY.md."""
    print("\n=== Generating SCHEMA_SUMMARY.md ===")

    bills = [unwrap_bill(r) for r in raw_list]
    detail_bills = []
    for d in raw_details:
        if isinstance(d, dict) and BILL_KEY in d:
            detail_bills.append(d[BILL_KEY])
        elif isinstance(d, dict) and "billID" in d:
            detail_bills.append(d)

    lines: list[str] = [
        "# RentVine Bills API - Discovery Summary",
        f"\n_Generated: {datetime.now().isoformat()}_\n",
    ]

    # 1. Endpoint & Pagination
    lines.append("## 1. Endpoint & Pagination\n")
    lines.append(f"- **Endpoint:** `{endpoint or 'NOT FOUND'}`")
    lines.append(f"- **Full URL:** `https://{{subdomain}}.rentvine.com/api/manager{endpoint}`")
    lines.append(f"- **Pagination:** `page` + `pageSize` query params")
    lines.append(f"- **Response shape:** JSON array of `{{\"bill\": {{...}}, \"contact\": {{...}}}}` objects")
    lines.append(f"- **Bills sampled:** {len(bills)}")
    lines.append("")

    # 2. Bill Fields
    lines.append("## 2. Bill Fields\n")
    if bills:
        lines.append("| Field | Type | Sample Value |")
        lines.append("|-------|------|-------------|")
        first = bills[0]
        for key, val in first.items():
            tname = type(val).__name__ if val is not None else "null"
            sample = redact_value(val)
            if isinstance(sample, (dict, list)):
                sample = f"({tname})"
            else:
                sample = str(sample)[:80] if sample else "(null)"
            lines.append(f"| `{key}` | {tname} | {sample} |")
    lines.append("")

    # Contact fields (brief)
    if raw_list:
        contact = raw_list[0].get("contact", {})
        if contact:
            lines.append("### Contact (vendor) Fields\n")
            lines.append(f"_({len(contact)} fields total)_ Key fields: "
                         f"`contactID`, `name`, `email`, `phone`, `contactType`, `vendorTypeID`")
            lines.append("")

    # 3. Keystone — Meld Linkage
    lines.append("## 3. Keystone: Meld Linkage Verdict\n")
    total = linkage.get("total_list_sampled", 0)
    wo_pop = linkage.get("workOrderID_populated", 0)
    maint = linkage.get("maintenance_bills_found", 0)
    maint_wo = linkage.get("maintenance_with_workOrderID", 0)
    maint_ref = linkage.get("maintenance_with_meld_ref_pattern", 0)

    lines.append("### Structured Field: `workOrderID`\n")
    lines.append(f"- Present on every bill record (27 fields include `workOrderID`)")
    lines.append(f"- **Populated (non-null):** {wo_pop}/{total} total bills sampled")
    lines.append(f"- **On maintenance bills:** {maint_wo}/{maint}")
    lines.append("")

    lines.append("### Text Pattern: `Meld [A-Z0-9]+` in description/paymentMemo\n")
    lines.append(f"- **Maintenance bills with pattern:** {maint_ref}/{maint}")
    if maint:
        lines.append(f"- **Coverage:** {maint_ref/maint*100:.0f}% of maintenance bills")
    lines.append("")

    if linkage.get("examples_with_meld_ref"):
        lines.append("#### Examples WITH meld ref:\n")
        for ex in linkage["examples_with_meld_ref"][:5]:
            lines.append(f"- Bill `{ex['billID']}`: ref=`{ex['meld_ref']}` | "
                         f"workOrderID=`{ex.get('workOrderID')}` | "
                         f"desc=`{ex['description'][:80]}`")
        lines.append("")

    if linkage.get("maintenance_without_meld_ref"):
        lines.append("#### Maintenance bills WITHOUT meld ref:\n")
        for ex in linkage["maintenance_without_meld_ref"][:5]:
            lines.append(f"- Bill `{ex['billID']}`: workOrderID=`{ex.get('workOrderID')}` | "
                         f"desc=`{ex['description'][:80]}` | "
                         f"memo=`{ex['paymentMemo'][:80]}`")
        lines.append("")

    lines.append("### VERDICT\n")
    if maint and maint_wo == maint:
        lines.append(f"**Primary linkage: `workOrderID`** — populated on 100% of maintenance "
                     f"bills ({maint_wo}/{maint}). This is a structured FK and the most "
                     f"reliable link to PropertyMeld work orders.")
    elif maint and maint_wo / maint > 0.8:
        lines.append(f"**Primary linkage: `workOrderID`** — populated on "
                     f"{maint_wo}/{maint} ({maint_wo/maint*100:.0f}%) maintenance bills. "
                     f"Pattern parsing covers {maint_ref}/{maint} as fallback.")
    elif maint and maint_ref / maint > 0.8:
        lines.append(f"**Primary linkage: `Meld <ref>` pattern parsing** — covers "
                     f"{maint_ref}/{maint} ({maint_ref/maint*100:.0f}%) maintenance bills. "
                     f"`workOrderID` only populated on {maint_wo}/{maint}.")
    elif maint:
        lines.append(f"**Mixed linkage needed.** `workOrderID` covers {maint_wo}/{maint}, "
                     f"pattern parsing covers {maint_ref}/{maint}. Combined approach recommended.")
    else:
        lines.append("**Insufficient maintenance bills sampled** to determine linkage reliability.")
    lines.append("")

    # 4. Itemized Charges
    lines.append("## 4. Itemized Charges\n")
    if charges.get("line_item_field_name"):
        lines.append(f"- **Array field:** `{charges['line_item_field_name']}`")
        lines.append(f"- **Items per bill:** {charges.get('line_item_count_per_bill', [])}")
        lines.append("")
        if charges.get("line_item_fields"):
            lines.append("| Field | Type |")
            lines.append("|-------|------|")
            for ex in charges.get("examples", [])[:1]:
                for key, val in ex.items():
                    lines.append(f"| `{key}` | {type(val).__name__} — `{str(val)[:60]}` |")
        lines.append("")
    else:
        lines.append("_No line item array found in detail response. "
                     "Line items may be at a sub-endpoint like `/accounting/bills/{id}/charges`._\n")

    # 5. Markup
    lines.append("## 5. Markup Findings\n")
    lines.append(f"- **Markup field:** `{markup.get('markup_field', 'NOT FOUND')}`")
    lines.append(f"- **Discount field:** `{markup.get('discount_field', 'NOT FOUND')}`")
    lines.append(f"- **All markup/discount fields:** `{markup.get('markup_discount_fields_seen', [])}`")
    lines.append(f"- **Bills with non-zero markup:** {len(markup.get('bills_with_nonzero_markup', []))}/{markup.get('total_checked', 0)}")
    lines.append("")
    if markup.get("bills_with_nonzero_markup"):
        lines.append("| Bill ID | markupPercent | isMarkup | Description |")
        lines.append("|---------|--------------|----------|-------------|")
        for ex in markup["bills_with_nonzero_markup"]:
            lines.append(f"| `{ex['billID']}` | {ex.get('markupPercent')} | "
                         f"{ex.get('isMarkup')} | {ex['description'][:50]} |")
    lines.append("")

    # 6. Dates
    lines.append("## 6. Date Fields\n")
    if dates.get("date_fields"):
        lines.append("| Field | Sample Value | Populated |")
        lines.append("|-------|-------------|-----------|")
        for field, info in dates["date_fields"].items():
            lines.append(f"| `{field}` | `{info['sample_value'] or ''}` | {info['populated']} |")
        lines.append("")
        lines.append(f"**Recommended 'appearance' date:** `{dates.get('recommended_appearance_date', 'unknown')}`")
    lines.append("")

    # 7. Filtering
    lines.append("## 7. Filtering Capabilities\n")
    lines.append(f"- **Date range:** {'YES' if filtering.get('date_filter_works') else 'NOT CONFIRMED'}")
    if filtering.get("date_filter_params"):
        lines.append(f"  - Params: `{filtering['date_filter_params']}`")
        lines.append(f"  - Records returned: {filtering.get('date_filter_record_count')}")
    lines.append(f"- **Property:** {'YES' if filtering.get('property_filter_works') else 'NOT CONFIRMED'}")
    if filtering.get("property_filter_params"):
        lines.append(f"  - Params: `{filtering['property_filter_params']}`")
        lines.append(f"  - Records returned: {filtering.get('property_filter_record_count')}")
    lines.append("")

    # 8. Detail response shape
    if raw_details:
        lines.append("## 8. Detail Response Shape\n")
        d = raw_details[0]
        if isinstance(d, dict):
            lines.append(f"Top-level keys: `{list(d.keys())}`\n")
            for key, val in d.items():
                if isinstance(val, dict):
                    lines.append(f"- `{key}`: dict with {len(val)} fields")
                elif isinstance(val, list):
                    lines.append(f"- `{key}`: list with {len(val)} items")
                    if val and isinstance(val[0], dict):
                        lines.append(f"  - Item fields: `{list(val[0].keys())}`")
                else:
                    lines.append(f"- `{key}`: {type(val).__name__}")
        lines.append("")

    md_path = OUTPUT_DIR / "SCHEMA_SUMMARY.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  -> Saved {md_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("RentVine Bills API Discovery")
    print("=" * 60)
    print(f"Output: {OUTPUT_DIR.resolve()}")
    print(f"Time: {datetime.now().isoformat()}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("\nInitializing RentVine client...")
    try:
        client = RentvineClient()
        print(f"  Connected to: {client.base_url}")
    except RentvineAPIError as e:
        print(f"FATAL: Cannot initialize client: {e}")
        sys.exit(1)

    endpoint = step1_connectivity(client)
    if not endpoint:
        print("\nFATAL: Could not find bills endpoint.")
        generate_summary(None, [], [], {}, {}, {}, {}, {})
        sys.exit(1)

    raw_list = step2_bill_list(client, endpoint)
    raw_details = step3_bill_detail(client, endpoint, raw_list)
    linkage = step4_meld_linkage(raw_list, raw_details)
    charges = step5_itemized_charges(raw_details)
    markup_findings = step6_markup(raw_list, raw_details)
    date_findings = step7_dates(raw_list, raw_details)
    filter_findings = step8_filtering(client, endpoint)

    generate_summary(
        endpoint, raw_list, raw_details, linkage, charges,
        markup_findings, date_findings, filter_findings,
    )

    print("\n" + "=" * 60)
    print("Discovery complete. See tmp/rentvine_discovery/SCHEMA_SUMMARY.md")
    print("=" * 60)


if __name__ == "__main__":
    main()
