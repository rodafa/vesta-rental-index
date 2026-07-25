"""
LeadSimple /deals helpers — find by email and move stage.

Separate from client.py (read-only /processes + /tasks) by design.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.leadsimple.com/rest"


def _get_auth():
    """Return (base_url, headers) or raise if the API key is missing."""
    api_key = getattr(settings, "LEADSIMPLE_API_KEY", "") or ""
    if not api_key:
        logger.error("leadsimple_key_not_configured")
        raise RuntimeError("LEADSIMPLE_API_KEY is not configured")

    base_url = (
        getattr(settings, "LEADSIMPLE_BASE_URL", "") or DEFAULT_BASE_URL
    ).rstrip("/")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    return base_url, headers


def find_deal_by_email(email: str, pipeline_id: str) -> dict:
    """Search /deals for a single deal whose contact email matches exactly.

    The API's search param is fuzzy, so we post-filter on exact email match
    (case-insensitive, stripped).

    Returns:
        {"status": "matched", "deal": <record>}  — exactly one match
        {"status": "no_match"}                    — zero matches
        {"status": "multiple", "count": n}        — ambiguous, caller must not pick
    """
    base_url, headers = _get_auth()
    target = email.strip().lower()

    resp = requests.get(
        f"{base_url}/deals",
        headers=headers,
        params={"search": email, "pipeline_id": pipeline_id},
        timeout=10,
    )
    if resp.status_code != 200:
        logger.error(
            "leadsimple_deal_search_failed",
            extra={"status": resp.status_code, "body": resp.text[:500]},
        )
        raise RuntimeError(
            f"LeadSimple /deals search returned {resp.status_code}"
        )

    records = resp.json().get("data") or []

    # Exact-match filter: compare against every email in every contact
    exact = []
    for deal in records:
        for contact in deal.get("contacts") or []:
            for addr in contact.get("emails") or []:
                if addr.strip().lower() == target:
                    exact.append(deal)
                    break
            else:
                continue
            break

    if len(exact) == 1:
        return {"status": "matched", "deal": exact[0]}
    if len(exact) == 0:
        return {"status": "no_match"}
    return {"status": "multiple", "count": len(exact)}


def move_deal_to_stage(deal_id: str, stage_id: str, pipeline_id: str) -> None:
    """PUT /deals/{deal_id} to change its stage. Raises on failure."""
    base_url, headers = _get_auth()

    resp = requests.put(
        f"{base_url}/deals/{deal_id}",
        headers=headers,
        json={"stage_id": stage_id, "pipeline_id": pipeline_id},
        timeout=10,
    )
    if not (200 <= resp.status_code < 300):
        logger.error(
            "leadsimple_stage_update_failed",
            extra={
                "deal_id": deal_id,
                "status": resp.status_code,
                "body": resp.text[:500],
            },
        )
        raise RuntimeError(
            f"LeadSimple stage update returned {resp.status_code}"
        )
