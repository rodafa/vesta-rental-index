"""
LeadSimple API Probe — read-only, standalone, one-off validation.
Reads LEADSIMPLE_API_KEY and optional LEADSIMPLE_BASE_URL from env.
GET requests only. No DB, no Django, no imports from the codebase.
"""
import json
import os
import sys
from collections import Counter

import requests

API_KEY = os.environ.get("LEADSIMPLE_API_KEY", "")
BASE_URL = (os.environ.get("LEADSIMPLE_BASE_URL") or "https://api.leadsimple.com/rest").rstrip("/")
MAX_PAGES = 20

def main():
    # Step 1: key check
    if not API_KEY:
        print("LEADSIMPLE_API_KEY is NOT set in the environment. Stopping.")
        sys.exit(0)
    print("LEADSIMPLE_API_KEY is set (value redacted).")
    print(f"Base URL: {BASE_URL}")
    print()

    headers = {"Authorization": f"Bearer {API_KEY}"}
    all_processes = []

    # Step 2: first request
    url = f"{BASE_URL}/processes"
    print(f"GET {url}?per_page=100&page=1 ...")
    resp = requests.get(url, headers=headers, params={"per_page": 100, "page": 1}, timeout=30)
    print(f"HTTP {resp.status_code}")
    if resp.status_code >= 300:
        print(f"Non-2xx response body:\n{resp.text[:2000]}")
        sys.exit(1)

    data = resp.json()
    items = data.get("data") or []
    meta = data.get("meta") or {}
    total_pages = meta.get("total_pages") or 1
    total_count = meta.get("total_count") or meta.get("total") or "unknown"
    all_processes.extend(items)

    # Step 3: paginate (capped)
    pages_to_fetch = min(total_pages, MAX_PAGES)
    for page in range(2, pages_to_fetch + 1):
        r = requests.get(url, headers=headers, params={"per_page": 100, "page": page}, timeout=30)
        if r.status_code >= 300:
            print(f"Page {page}: HTTP {r.status_code}, stopping pagination.")
            break
        page_data = r.json()
        page_items = page_data.get("data") or []
        if not page_items:
            break
        all_processes.extend(page_items)

    print(f"\nTotal processes reported by API: {total_count}")
    print(f"Pages available: {total_pages} | Pages fetched: {pages_to_fetch}")
    print(f"Processes fetched: {len(all_processes)}")

    if not all_processes:
        print("\nNo processes returned. Nothing more to analyze.")
        sys.exit(0)

    # Step 4: full first process
    print("\n" + "=" * 70)
    print("FIRST PROCESS OBJECT (full JSON):")
    print("=" * 70)
    print(json.dumps(all_processes[0], indent=2, default=str))

    # Also print meta block from first response
    print("\n" + "=" * 70)
    print("META BLOCK (from page 1 response):")
    print("=" * 70)
    print(json.dumps(meta, indent=2, default=str))

    # Step 5: aggregations
    print("\n" + "=" * 70)
    print("AGGREGATIONS")
    print("=" * 70)

    # 5a: pipeline_id / pipeline_name pairs
    pipeline_counter = Counter()
    for p in all_processes:
        pid = p.get("pipeline_id") or p.get("pipeline", {}).get("id") if isinstance(p.get("pipeline"), dict) else p.get("pipeline_id")
        pname = p.get("pipeline_name") or (p.get("pipeline", {}).get("name") if isinstance(p.get("pipeline"), dict) else None)
        pipeline_counter[(pid, pname)] += 1
    print("\n--- Pipeline (id, name) pairs ---")
    if pipeline_counter:
        for (pid, pname), count in pipeline_counter.most_common():
            print(f"  ({pid}, {pname!r}): {count}")
    else:
        print("  No pipeline_id or pipeline_name found on any process.")
        # Check what keys exist at top level
        print("  Top-level keys on first process:", sorted(all_processes[0].keys()))

    # 5b: stage_name values
    stage_counter = Counter()
    for p in all_processes:
        stage = p.get("stage") or {}
        if isinstance(stage, dict):
            sname = stage.get("name") or "(no stage.name)"
        else:
            sname = f"(stage is {type(stage).__name__}: {str(stage)[:80]})"
        stage_counter[sname] += 1
    print("\n--- Distinct stage.name values ---")
    for sname, count in stage_counter.most_common():
        print(f"  {sname}: {count}")

    # 5c: comments type distribution
    comments_type_counter = Counter()
    first_list_keys = None
    for p in all_processes:
        c = p.get("comments")
        if c is None:
            comments_type_counter["null/absent"] += 1
        elif isinstance(c, str):
            comments_type_counter["string"] += 1
        elif isinstance(c, list):
            comments_type_counter["list"] += 1
            if first_list_keys is None and c:
                first_list_keys = sorted(c[0].keys()) if isinstance(c[0], dict) else f"(element type: {type(c[0]).__name__})"
        else:
            comments_type_counter[f"other({type(c).__name__})"] += 1
    print("\n--- comments type distribution ---")
    for ctype, count in comments_type_counter.most_common():
        print(f"  {ctype}: {count}")
    if first_list_keys:
        print(f"  First list element keys: {first_list_keys}")

    # Also check if "comments" key even exists
    has_comments_key = sum(1 for p in all_processes if "comments" in p)
    print(f"  Processes with 'comments' key present: {has_comments_key}/{len(all_processes)}")

    # 5d: properties array
    props_len_counter = Counter()
    sample_addresses = []
    unit_like_addresses = []
    for p in all_processes:
        props = p.get("properties") or []
        props_len_counter[len(props)] += 1
        for prop in props:
            addr = prop.get("address") or prop.get("street") or prop.get("name") or ""
            if addr and len(sample_addresses) < 10:
                sample_addresses.append(addr)
            if addr:
                addr_lower = addr.lower()
                if any(kw in addr_lower for kw in ["apt", "unit", "suite", "#", "ste"]):
                    if len(unit_like_addresses) < 5:
                        unit_like_addresses.append(addr)
    print("\n--- properties[] array length distribution ---")
    for length, count in sorted(props_len_counter.items()):
        print(f"  {length} properties: {count} processes")
    print(f"\n  Sample addresses (up to 10):")
    for addr in sample_addresses:
        print(f"    {addr}")
    if unit_like_addresses:
        print(f"\n  Unit-like addresses detected ({len(unit_like_addresses)}):")
        for addr in unit_like_addresses:
            print(f"    {addr}")
    else:
        print("\n  No unit-like addresses detected (no apt/unit/suite/# keywords).")

    # 5e: field presence
    print("\n--- Field presence ---")
    for field_path in ["name", "created_at", "closed_at"]:
        populated = sum(1 for p in all_processes if p.get(field_path))
        print(f"  {field_path}: {populated}/{len(all_processes)} populated")
    # stage.name separately
    stage_name_populated = sum(
        1 for p in all_processes
        if isinstance(p.get("stage"), dict) and p["stage"].get("name")
    )
    print(f"  stage.name: {stage_name_populated}/{len(all_processes)} populated")

    # Bonus: list all top-level keys across all processes
    all_keys = set()
    for p in all_processes:
        all_keys.update(p.keys())
    print(f"\n--- All top-level keys (union across all processes) ---")
    print(f"  {sorted(all_keys)}")

    print("\n--- Done ---")


if __name__ == "__main__":
    main()
