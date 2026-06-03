"""
LeadSimple REST API client.

Stateless: fetches /processes with Bearer auth, paginated.
Returns raw process dicts or a degraded sentinel on failure.
"""

import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Sentinel returned when the API is unavailable or unconfigured.
# Callers check `result is DEGRADED` to distinguish from an empty list.
DEGRADED = object()

DEFAULT_BASE_URL = "https://api.leadsimple.com/rest"
DEFAULT_PER_PAGE = 100
DEFAULT_MAX_PAGES = 20
MAX_RETRIES = 3
BACKOFF_FACTOR = 2  # seconds: 2, 4, 8
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def fetch_processes(*, max_pages=None):
    """
    Fetch all /processes from the LeadSimple API.

    Returns list[dict] on success, or the DEGRADED sentinel when the API
    key is missing or the API call fails after retries.
    """
    api_key = getattr(settings, "LEADSIMPLE_API_KEY", "") or ""
    if not api_key:
        logger.warning(
            "leadsimple_api_key_missing",
            extra={"detail": "LEADSIMPLE_API_KEY not configured"},
        )
        return DEGRADED

    base_url = (
        getattr(settings, "LEADSIMPLE_BASE_URL", "") or DEFAULT_BASE_URL
    ).rstrip("/")
    page_cap = max_pages or DEFAULT_MAX_PAGES

    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    all_processes = []

    page = 1
    while True:
        data = _get_page(base_url, headers, page)
        if data is DEGRADED:
            return DEGRADED

        items = data.get("data") or []
        if not items:
            break

        all_processes.extend(items)

        meta = data.get("meta") or {}
        total_pages = meta.get("total_pages") or 1

        if total_pages > page_cap:
            logger.warning(
                "leadsimple_page_cap_hit",
                extra={
                    "total_pages": total_pages,
                    "max_pages": page_cap,
                    "fetched_so_far": len(all_processes),
                },
            )

        if page >= min(total_pages, page_cap):
            break

        page += 1

    logger.info(
        "leadsimple_fetch_complete",
        extra={"total_fetched": len(all_processes), "pages": page},
    )
    return all_processes


def _get_page(base_url, headers, page):
    """Fetch a single page with retry logic. Returns parsed JSON or DEGRADED."""
    url = f"{base_url}/processes"
    params = {"per_page": DEFAULT_PER_PAGE, "page": page}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
        except requests.RequestException as exc:
            logger.warning(
                "leadsimple_request_error",
                extra={"page": page, "attempt": attempt, "error": str(exc)},
            )
            if attempt < MAX_RETRIES:
                time.sleep(BACKOFF_FACTOR ** attempt)
                continue
            return DEGRADED

        if resp.status_code < 300:
            return resp.json()

        if resp.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES:
            logger.warning(
                "leadsimple_retryable_error",
                extra={
                    "page": page,
                    "attempt": attempt,
                    "status_code": resp.status_code,
                },
            )
            time.sleep(BACKOFF_FACTOR ** attempt)
            continue

        logger.error(
            "leadsimple_api_error",
            extra={
                "page": page,
                "status_code": resp.status_code,
                "body": resp.text[:500],
            },
        )
        return DEGRADED

    return DEGRADED
