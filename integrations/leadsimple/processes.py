"""
LeadSimple /processes write helper — create application processes.

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


def create_process(name: str, process_type_id: str, stage_id: str) -> dict:
    """POST /processes. Returns the created process dict. Raises on failure."""
    base_url, headers = _get_auth()

    resp = requests.post(
        f"{base_url}/processes",
        headers=headers,
        json={"process": {
            "name": name,
            "process_type_id": process_type_id,
            "stage_id": stage_id,
        }},
        timeout=30,
    )
    if not (200 <= resp.status_code < 300):
        raise RuntimeError(
            f"LeadSimple /processes returned {resp.status_code}: {resp.text[:500]}"
        )
    data = resp.json()
    return data.get("data") if isinstance(data.get("data"), dict) else data
