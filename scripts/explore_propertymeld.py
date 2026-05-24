"""
PropertyMeld v2 API Discovery Script
=====================================
Read-only (GET only) reconnaissance of the PropertyMeld API.
Outputs raw JSON to tmp/propertymeld_discovery/ and a markdown summary.

Usage:
    uv run python scripts/explore_propertymeld.py
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

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_URL = "https://api.propertymeld.com/api/v2"
OUTPUT_DIR = Path("tmp/propertymeld_discovery")
DELAY_BETWEEN_REQUESTS = 0.5  # seconds

# PII patterns to redact in the summary
PII_PATTERNS = [
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
]


OAUTH_TOKEN_URL = f"{BASE_URL}/oauth/token/"


def get_bearer_token() -> str:
    """Get a bearer token. Tries direct key first, then OAuth2 client credentials."""
    # Option 1: Direct bearer token
    key = os.environ.get("PROPERTYMELD_API_KEY", "").strip()
    if key:
        print("  Using PROPERTYMELD_API_KEY directly.")
        return key

    # Option 2: OAuth2 client credentials flow
    client_id = os.environ.get("PROPERTY_MELD_CLIENT_ID", "").strip()
    client_secret = os.environ.get("PROPERTY_MELD_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        print("  Obtaining token via OAuth2 client_credentials flow...")
        try:
            resp = httpx.post(
                OAUTH_TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=30.0,
            )
            if resp.status_code == 200:
                body = resp.json()
                token = body.get("access_token", "")
                if token:
                    print("  OAuth2 token obtained successfully.")
                    return token
                print(f"  ERROR: Token response missing access_token: {list(body.keys())}")
            else:
                print(f"  ERROR: OAuth2 token request failed ({resp.status_code}): {resp.text[:300]}")
        except httpx.RequestError as e:
            print(f"  ERROR: OAuth2 token request failed: {e}")

    print("ERROR: No credentials available.")
    print("  Set PROPERTYMELD_API_KEY (bearer token) or")
    print("  PROPERTY_MELD_CLIENT_ID + PROPERTY_MELD_CLIENT_SECRET (OAuth2).")
    sys.exit(1)


def get_multitenant_id(token: str) -> int | None:
    """Fetch the X-Multitenant-Id from /api/v2/management/."""
    print("  Fetching management org (X-Multitenant-Id)...")
    try:
        resp = httpx.get(
            f"{BASE_URL}/management/",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            timeout=30.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            # Could be a list of orgs or a dict
            if isinstance(data, list) and data:
                org_id = data[0].get("multitenant_id") or data[0].get("id")
                print(f"  Found org ID: {org_id}")
                save_json("management_orgs.json", data)
                return org_id
            elif isinstance(data, dict):
                # Paginated response
                results = data.get("results", data.get("data", []))
                if isinstance(results, list) and results:
                    org_id = results[0].get("multitenant_id") or results[0].get("id")
                    print(f"  Found org ID: {org_id}")
                    save_json("management_orgs.json", data)
                    return org_id
                elif "multitenant_id" in data:
                    org_id = data["multitenant_id"]
                    print(f"  Found org ID: {org_id}")
                    save_json("management_orgs.json", data)
                    return org_id
        else:
            print(f"  WARNING: /management/ returned {resp.status_code} — will try without X-Multitenant-Id")
    except httpx.RequestError as e:
        print(f"  WARNING: /management/ request failed: {e}")
    return None


def redact_pii(value: str) -> str:
    for pattern, replacement in PII_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def redact_value_for_summary(value: Any) -> Any:
    if isinstance(value, str):
        return redact_pii(value)
    return value


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
class APIClient:
    def __init__(self, token: str, multitenant_id: int | None = None):
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if multitenant_id is not None:
            headers["X-Multitenant-Id"] = str(multitenant_id)
        self.client = httpx.Client(
            base_url=BASE_URL,
            headers=headers,
            timeout=30.0,
        )
        self.rate_limit_headers: dict[str, str] = {}

    def get(self, path: str, params: dict | None = None) -> dict | list | None:
        """GET request. Returns parsed JSON or None on failure."""
        time.sleep(DELAY_BETWEEN_REQUESTS)
        url = path if path.startswith("http") else path
        try:
            resp = self.client.get(url, params=params)
        except httpx.RequestError as e:
            print(f"  [REQUEST ERROR] {path}: {e}")
            return None

        # Capture rate-limit headers
        for header in resp.headers:
            if "rate" in header.lower() or "limit" in header.lower() or "retry" in header.lower():
                self.rate_limit_headers[header] = resp.headers[header]

        if resp.status_code == 404:
            print(f"  [404] {path} — not found")
            return None
        if resp.status_code == 401:
            print(f"  [401] {path} — unauthorized")
            return None
        if resp.status_code == 403:
            print(f"  [403] {path} — forbidden")
            return None
        if resp.status_code >= 400:
            print(f"  [{resp.status_code}] {path} — {resp.text[:200]}")
            return None

        try:
            return resp.json()
        except json.JSONDecodeError:
            print(f"  [PARSE ERROR] {path} — not valid JSON")
            return None

    def close(self):
        self.client.close()


# ---------------------------------------------------------------------------
# File output helpers
# ---------------------------------------------------------------------------
def save_json(filename: str, data: Any) -> None:
    filepath = OUTPUT_DIR / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"  -> Saved {filepath}")


# ---------------------------------------------------------------------------
# Discovery steps
# ---------------------------------------------------------------------------
def step1_connectivity(client: APIClient) -> bool:
    """Authenticate and confirm connectivity."""
    print("\n=== Step 1: Connectivity check (/meld/) ===")
    data = client.get("/meld/")
    if data is None:
        print("FAILED: Cannot connect to /meld/ endpoint.")
        return False
    save_json("step1_connectivity.json", data)
    print("  OK: Connected successfully.")
    return True


def step2_meld_list(client: APIClient) -> list[dict]:
    """Capture pagination structure and ~10 sample melds."""
    print("\n=== Step 2: Meld list (/meld/) ===")
    data = client.get("/meld/", params={"page_size": 10})
    if data is None:
        data = client.get("/meld/", params={"limit": 10})
    if data is None:
        print("  FAILED to fetch meld list.")
        return []

    save_json("step2_meld_list.json", data)

    # Extract melds from response (handle different pagination shapes)
    melds = []
    if isinstance(data, list):
        melds = data[:10]
    elif isinstance(data, dict):
        for key in ("results", "data", "melds", "items"):
            if key in data and isinstance(data[key], list):
                melds = data[key][:10]
                break
        if not melds and "id" in data:
            melds = [data]

    print(f"  Found {len(melds)} melds in response.")
    return melds


def step3_meld_detail(client: APIClient, melds: list[dict]) -> list[dict]:
    """Fetch full detail for ~5 melds (mix of open/closed)."""
    print("\n=== Step 3: Meld detail (/meld/{{id}}/) ===")
    details = []
    if not melds:
        print("  No melds available to fetch detail for.")
        return details

    # Try to get a mix of statuses
    ids_to_fetch = []
    status_field = None
    for field in ("status", "stage", "state", "meld_status"):
        if any(field in m for m in melds):
            status_field = field
            break

    if status_field:
        open_ids = [m["id"] for m in melds if m.get(status_field, "").lower() not in ("closed", "completed", "complete")]
        closed_ids = [m["id"] for m in melds if m.get(status_field, "").lower() in ("closed", "completed", "complete")]
        ids_to_fetch = open_ids[:3] + closed_ids[:2]

    if len(ids_to_fetch) < 5:
        ids_to_fetch = [m["id"] for m in melds[:5]]

    for meld_id in ids_to_fetch[:5]:
        print(f"  Fetching meld {meld_id}...")
        detail = client.get(f"/meld/{meld_id}/")
        if detail:
            details.append(detail)
            save_json(f"step3_meld_detail_{meld_id}.json", detail)

    print(f"  Fetched {len(details)} meld details.")
    return details


def step4_probe_fields(client: APIClient, melds: list[dict], details: list[dict]) -> dict:
    """Probe for specific data points."""
    print("\n=== Step 4: Probe specific fields/endpoints ===")
    findings: dict[str, Any] = {}
    all_melds = details if details else melds

    # 4a: Status/stage field — find distinct values
    print("  4a: Status/stage values...")
    status_values = set()
    status_field_name = None
    for field in ("status", "stage", "state", "meld_status", "status_name"):
        vals = [m.get(field) for m in all_melds if m.get(field) is not None]
        if vals:
            status_field_name = field
            status_values.update(str(v) for v in vals)
    findings["status"] = {
        "field_name": status_field_name,
        "distinct_values": sorted(status_values),
        "found": bool(status_field_name),
    }

    # 4b: Property + unit linkage
    print("  4b: Property + unit linkage...")
    prop_fields = set()
    unit_fields = set()
    for m in all_melds:
        for key in m:
            if "property" in key.lower() or "prop" in key.lower():
                prop_fields.add(key)
            if "unit" in key.lower():
                unit_fields.add(key)
        # Check nested
        if isinstance(m.get("property"), dict):
            prop_fields.add("property (nested object)")
        if isinstance(m.get("unit"), dict):
            unit_fields.add("unit (nested object)")
    findings["property_linkage"] = {
        "found": bool(prop_fields),
        "fields": sorted(prop_fields),
        "location": "nested on meld" if prop_fields else "not found",
    }
    findings["unit_linkage"] = {
        "found": bool(unit_fields),
        "fields": sorted(unit_fields),
        "location": "nested on meld" if unit_fields else "not found",
    }

    # 4c: Owner linkage
    print("  4c: Owner linkage...")
    owner_fields = set()
    for m in all_melds:
        for key in m:
            if "owner" in key.lower():
                owner_fields.add(key)
        if isinstance(m.get("owner"), dict):
            owner_fields.add("owner (nested object)")
    findings["owner_linkage"] = {
        "found": bool(owner_fields),
        "fields": sorted(owner_fields),
        "location": "nested on meld" if owner_fields else "not found",
    }

    # 4d: Messages / conversation thread
    print("  4d: Messages / conversation thread...")
    message_info = {"found": False, "location": "not found"}
    for m in all_melds:
        for key in m:
            if "message" in key.lower() or "conversation" in key.lower() or "thread" in key.lower() or "comment" in key.lower():
                message_info = {"found": True, "location": f"nested on meld (field: {key})"}
                break
    # Try sub-resource
    if not message_info["found"] and all_melds:
        meld_id = all_melds[0].get("id")
        if meld_id:
            for sub in ("messages", "comments", "conversations", "activity"):
                data = client.get(f"/meld/{meld_id}/{sub}/")
                if data is not None:
                    message_info = {"found": True, "location": f"/meld/{{id}}/{sub}/"}
                    save_json(f"step4_meld_{meld_id}_{sub}.json", data)
                    break
    findings["messages"] = message_info

    # 4e: Internal notes / activity log
    print("  4e: Internal notes / activity log...")
    notes_info = {"found": False, "location": "not found"}
    for m in all_melds:
        for key in m:
            if "note" in key.lower() or "activity" in key.lower() or "log" in key.lower():
                notes_info = {"found": True, "location": f"nested on meld (field: {key})"}
                break
    if not notes_info["found"] and all_melds:
        meld_id = all_melds[0].get("id")
        if meld_id:
            for sub in ("notes", "activity", "log", "internal-notes"):
                data = client.get(f"/meld/{meld_id}/{sub}/")
                if data is not None:
                    notes_info = {"found": True, "location": f"/meld/{{id}}/{sub}/"}
                    save_json(f"step4_meld_{meld_id}_{sub}.json", data)
                    break
    findings["notes_activity"] = notes_info

    # 4f: Vendor / assignment info
    print("  4f: Vendor / assignment info...")
    vendor_fields = set()
    for m in all_melds:
        for key in m:
            if "vendor" in key.lower() or "assign" in key.lower() or "technician" in key.lower():
                vendor_fields.add(key)
    findings["vendor_assignment"] = {
        "found": bool(vendor_fields),
        "fields": sorted(vendor_fields),
        "location": "nested on meld" if vendor_fields else "not found",
    }
    # Also try /vendor/ endpoint
    vendor_list = client.get("/vendor/")
    if vendor_list is not None:
        findings["vendor_assignment"]["endpoint"] = "/vendor/"
        save_json("step4_vendor_list.json", vendor_list)

    # 4g: Date fields
    print("  4g: Date fields...")
    date_fields = set()
    for m in all_melds:
        for key in m:
            if any(d in key.lower() for d in ("date", "created", "updated", "scheduled", "completed", "closed", "opened", "_at")):
                date_fields.add(key)
    findings["date_fields"] = {
        "found": bool(date_fields),
        "fields": sorted(date_fields),
    }

    # 4h: Cost — expenditures / invoices
    print("  4h: Cost / expenditures / invoices...")
    cost_info = {"found": False, "location": "not found", "fields": []}
    # Check nested on meld
    for m in all_melds:
        for key in m:
            if any(c in key.lower() for c in ("cost", "expense", "expenditure", "invoice", "total", "amount", "price")):
                cost_info["found"] = True
                cost_info["location"] = f"nested on meld (field: {key})"
                cost_info["fields"].append(key)
    # Try standalone endpoints
    for endpoint in ("/expenditure/", "/expenditures/", "/invoice/", "/invoices/"):
        data = client.get(endpoint)
        if data is not None:
            cost_info["found"] = True
            cost_info["location"] = endpoint
            save_json(f"step4_cost_{endpoint.strip('/').replace('/', '_')}.json", data)
            break
    # Try nested on meld
    if not cost_info["found"] and all_melds:
        meld_id = all_melds[0].get("id")
        if meld_id:
            for sub in ("expenditures", "invoices", "costs"):
                data = client.get(f"/meld/{meld_id}/{sub}/")
                if data is not None:
                    cost_info["found"] = True
                    cost_info["location"] = f"/meld/{{id}}/{sub}/"
                    save_json(f"step4_meld_{meld_id}_{sub}.json", data)
                    break
    findings["cost"] = cost_info

    save_json("step4_findings.json", findings)
    return findings


def step5_property_unit(client: APIClient) -> dict:
    """Probe /property/ and /unit/ endpoints."""
    print("\n=== Step 5: Property and Unit endpoints ===")
    results = {}

    # Property
    for endpoint in ("/property/", "/properties/"):
        data = client.get(endpoint, params={"page_size": 1, "limit": 1})
        if data is not None:
            results["property"] = {"endpoint": endpoint, "data": data}
            save_json("step5_property.json", data)
            break
    if "property" not in results:
        print("  Property endpoint not found at /property/ or /properties/")
        results["property"] = None

    # Unit
    for endpoint in ("/unit/", "/units/"):
        data = client.get(endpoint, params={"page_size": 1, "limit": 1})
        if data is not None:
            results["unit"] = {"endpoint": endpoint, "data": data}
            save_json("step5_unit.json", data)
            break
    if "unit" not in results:
        print("  Unit endpoint not found at /unit/ or /units/")
        results["unit"] = None

    return results


# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------
def infer_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        # Try to detect date strings
        if re.match(r"\d{4}-\d{2}-\d{2}", value):
            return "datetime/date"
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def build_field_table(obj: dict, max_sample_len: int = 60) -> list[tuple[str, str, str]]:
    """Returns list of (field_name, type, sample_value) tuples."""
    rows = []
    for key, value in sorted(obj.items()):
        vtype = infer_type(value)
        sample = str(redact_value_for_summary(value))
        if len(sample) > max_sample_len:
            sample = sample[:max_sample_len] + "..."
        rows.append((key, vtype, sample))
    return rows


def generate_summary(
    melds: list[dict],
    details: list[dict],
    findings: dict,
    prop_unit: dict,
    rate_limit_headers: dict,
) -> str:
    """Generate the SCHEMA_SUMMARY.md content."""
    lines = [
        "# PropertyMeld v2 API — Schema Discovery Summary",
        f"\nGenerated: {datetime.now().isoformat()}",
        f"\nBase URL: `{BASE_URL}`",
        "",
    ]

    # Endpoints hit
    lines.append("## Endpoints Discovered\n")
    lines.append("| Endpoint | Purpose | Status |")
    lines.append("|----------|---------|--------|")
    lines.append("| `/meld/` | List all melds (work orders) | OK |")
    lines.append("| `/meld/{id}/` | Meld detail | OK |")
    if prop_unit.get("property"):
        lines.append(f"| `{prop_unit['property']['endpoint']}` | Property list | OK |")
    else:
        lines.append("| `/property/` | Property list | Not found |")
    if prop_unit.get("unit"):
        lines.append(f"| `{prop_unit['unit']['endpoint']}` | Unit list | OK |")
    else:
        lines.append("| `/unit/` | Unit list | Not found |")
    if findings.get("vendor_assignment", {}).get("endpoint"):
        lines.append(f"| `{findings['vendor_assignment']['endpoint']}` | Vendor list | OK |")
    lines.append("")

    # Pagination structure
    lines.append("## Pagination Structure\n")
    if melds:
        # Read back the raw list response to describe pagination
        list_file = OUTPUT_DIR / "step2_meld_list.json"
        if list_file.exists():
            with open(list_file) as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                pagination_keys = [k for k in raw if k not in ("results", "data", "melds", "items")]
                lines.append(f"Response is a JSON object with keys: `{', '.join(sorted(raw.keys()))}`\n")
                lines.append("Pagination-related fields:")
                for k in pagination_keys:
                    lines.append(f"- `{k}`: `{redact_value_for_summary(raw[k])}`")
            else:
                lines.append("Response is a JSON array (no wrapper pagination object).")
    lines.append("")

    # Meld field table (from list view)
    if melds:
        lines.append("## Meld — List View Fields\n")
        lines.append("| Field | Type | Sample |")
        lines.append("|-------|------|--------|")
        for name, vtype, sample in build_field_table(melds[0]):
            lines.append(f"| `{name}` | {vtype} | `{sample}` |")
        lines.append("")

    # Meld detail field table
    if details:
        lines.append("## Meld — Detail View Fields\n")
        lines.append("| Field | Type | Sample |")
        lines.append("|-------|------|--------|")
        for name, vtype, sample in build_field_table(details[0]):
            lines.append(f"| `{name}` | {vtype} | `{sample}` |")
        lines.append("")

        # Nested objects
        for key, value in sorted(details[0].items()):
            if isinstance(value, dict) and value:
                lines.append(f"### Nested: `{key}`\n")
                lines.append("| Field | Type | Sample |")
                lines.append("|-------|------|--------|")
                for name, vtype, sample in build_field_table(value):
                    lines.append(f"| `{name}` | {vtype} | `{sample}` |")
                lines.append("")
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                lines.append(f"### Nested array: `{key}` (sample item)\n")
                lines.append("| Field | Type | Sample |")
                lines.append("|-------|------|--------|")
                for name, vtype, sample in build_field_table(value[0]):
                    lines.append(f"| `{name}` | {vtype} | `{sample}` |")
                lines.append("")

    # Status values
    lines.append("## Distinct Status/Stage Values\n")
    if findings.get("status", {}).get("found"):
        lines.append(f"Field name: `{findings['status']['field_name']}`\n")
        for v in findings["status"]["distinct_values"]:
            lines.append(f"- `{v}`")
    else:
        lines.append("No status/stage field identified.")
    lines.append("")

    # Step 4 probe results
    lines.append("## Data Point Probes (Step 4)\n")
    lines.append("| Data Point | Found | Location |")
    lines.append("|------------|-------|----------|")
    probe_items = [
        ("Status/stage field", findings.get("status", {})),
        ("Property linkage", findings.get("property_linkage", {})),
        ("Unit linkage", findings.get("unit_linkage", {})),
        ("Owner linkage", findings.get("owner_linkage", {})),
        ("Messages/conversation", findings.get("messages", {})),
        ("Internal notes/activity", findings.get("notes_activity", {})),
        ("Vendor/assignment", findings.get("vendor_assignment", {})),
        ("Date fields", findings.get("date_fields", {})),
        ("Cost/invoices", findings.get("cost", {})),
    ]
    for label, info in probe_items:
        found = "Yes" if info.get("found") else "No"
        location = info.get("location", info.get("fields", "—"))
        if isinstance(location, list):
            location = ", ".join(location) if location else "—"
        lines.append(f"| {label} | {found} | `{location}` |")
    lines.append("")

    # Property fields
    if prop_unit.get("property"):
        lines.append("## Property Endpoint Fields\n")
        pdata = prop_unit["property"]["data"]
        sample = None
        if isinstance(pdata, dict):
            for key in ("results", "data", "items"):
                if key in pdata and isinstance(pdata[key], list) and pdata[key]:
                    sample = pdata[key][0]
                    break
            if not sample and "id" in pdata:
                sample = pdata
        elif isinstance(pdata, list) and pdata:
            sample = pdata[0]
        if sample:
            lines.append("| Field | Type | Sample |")
            lines.append("|-------|------|--------|")
            for name, vtype, sval in build_field_table(sample):
                lines.append(f"| `{name}` | {vtype} | `{sval}` |")
        lines.append("")

    # Unit fields
    if prop_unit.get("unit"):
        lines.append("## Unit Endpoint Fields\n")
        udata = prop_unit["unit"]["data"]
        sample = None
        if isinstance(udata, dict):
            for key in ("results", "data", "items"):
                if key in udata and isinstance(udata[key], list) and udata[key]:
                    sample = udata[key][0]
                    break
            if not sample and "id" in udata:
                sample = udata
        elif isinstance(udata, list) and udata:
            sample = udata[0]
        if sample:
            lines.append("| Field | Type | Sample |")
            lines.append("|-------|------|--------|")
            for name, vtype, sval in build_field_table(sample):
                lines.append(f"| `{name}` | {vtype} | `{sval}` |")
        lines.append("")

    # Rate-limit headers
    lines.append("## Rate-Limit Headers Observed\n")
    if rate_limit_headers:
        for header, value in sorted(rate_limit_headers.items()):
            lines.append(f"- `{header}`: `{value}`")
    else:
        lines.append("No rate-limit headers observed in responses.")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("PropertyMeld v2 API Discovery")
    print("=" * 60)

    token = get_bearer_token()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    multitenant_id = get_multitenant_id(token)
    client = APIClient(token, multitenant_id)

    try:
        # Step 1
        if not step1_connectivity(client):
            print("\nAborting: cannot connect to API.")
            sys.exit(1)

        # Step 2
        melds = step2_meld_list(client)

        # Step 3
        details = step3_meld_detail(client, melds)

        # Step 4
        findings = step4_probe_fields(client, melds, details)

        # Step 5
        prop_unit = step5_property_unit(client)

        # Generate summary
        print("\n=== Generating summary ===")
        summary = generate_summary(melds, details, findings, prop_unit, client.rate_limit_headers)
        summary_path = OUTPUT_DIR / "SCHEMA_SUMMARY.md"
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(summary)
        print(f"  -> Summary written to {summary_path}")

        print("\n" + "=" * 60)
        print("Discovery complete. See tmp/propertymeld_discovery/")
        print("=" * 60)

    finally:
        client.close()


if __name__ == "__main__":
    main()
