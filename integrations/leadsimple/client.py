"""
LeadSimple REST API client.

Stateless: fetches /processes and /tasks with Bearer auth, paginated.
Returns raw dicts or a degraded sentinel on failure.
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
DEFAULT_TASKS_MAX_PAGES = 200
MAX_RETRIES = 4
BACKOFF_FACTOR = 2  # seconds: 2, 4, 8
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
INTER_PAGE_PAUSE = 1.0         # seconds between successive page fetches (pacing)
RATE_LIMIT_FALLBACK_WAIT = 90  # seconds to wait on 429 when no Retry-After header
RATE_LIMIT_MAX_WAIT = 120      # cap on an honored Retry-After value (safety)


def _get_auth():
    """Return (base_url, headers) or None if API key is missing."""
    api_key = getattr(settings, "LEADSIMPLE_API_KEY", "") or ""
    if not api_key:
        logger.warning(
            "leadsimple_api_key_missing",
            extra={"detail": "LEADSIMPLE_API_KEY not configured"},
        )
        return None

    base_url = (
        getattr(settings, "LEADSIMPLE_BASE_URL", "") or DEFAULT_BASE_URL
    ).rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    return base_url, headers


def fetch_processes(*, max_pages=None):
    """
    Fetch all /processes from the LeadSimple API.

    Returns list[dict] on success, or the DEGRADED sentinel when the API
    key is missing or the API call fails after retries.
    """
    auth = _get_auth()
    if auth is None:
        return DEGRADED

    base_url, headers = auth
    page_cap = max_pages or DEFAULT_MAX_PAGES

    all_processes = []

    page = 1
    while True:
        data = _get_page(base_url, headers, page, endpoint="/processes")
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


def fetch_tasks(*, period_start=None, max_pages=None):
    """
    Fetch /tasks from the LeadSimple API.

    When period_start is provided, adds `updated_since` param (unix epoch)
    with a 7-day buffer to scope results to the reporting period.

    Returns list[dict] on success, or DEGRADED on failure.
    Fail-loud on page cap: if total_pages > page_cap, logs ERROR and
    returns DEGRADED — never silently returns a truncated set.
    """
    auth = _get_auth()
    if auth is None:
        return DEGRADED

    base_url, headers = auth
    page_cap = max_pages or DEFAULT_TASKS_MAX_PAGES

    extra_params = {}
    if period_start is not None:
        from datetime import timedelta, timezone as dt_tz
        # Apply 7-day buffer before period_start for safety
        buffer_date = period_start - timedelta(days=7)
        from datetime import datetime, time as dt_time
        epoch_dt = datetime.combine(buffer_date, dt_time.min, tzinfo=dt_tz.utc)
        extra_params["updated_since"] = int(epoch_dt.timestamp())

    t0 = time.monotonic()
    all_tasks = []

    page = 1
    while True:
        data = _get_page(
            base_url, headers, page,
            endpoint="/tasks", extra_params=extra_params,
        )
        if data is DEGRADED:
            return DEGRADED

        items = data.get("data") or []
        if not items:
            break

        all_tasks.extend(items)

        meta = data.get("meta") or {}
        total_pages = meta.get("total_pages") or 1
        total_count = meta.get("total_count") or len(all_tasks)

        if total_pages > page_cap:
            logger.error(
                "leadsimple_tasks_page_cap_exceeded",
                extra={
                    "total_pages": total_pages,
                    "page_cap": page_cap,
                    "total_count": total_count,
                },
            )
            return DEGRADED

        if page >= total_pages:
            break

        page += 1

    elapsed = time.monotonic() - t0
    logger.info(
        "leadsimple_tasks_fetch_complete",
        extra={
            "total_fetched": len(all_tasks),
            "pages": page,
            "elapsed_seconds": round(elapsed, 2),
        },
    )
    return all_tasks


def _retry_wait_seconds(resp, status_code, attempt):
    """Seconds to wait before a retry.

    On 429 (rate limit) honor the server's window: prefer the Retry-After
    header, else fall back to a fixed patient wait. Other retryable codes
    (500/502/503/504) are transient blips and use exponential backoff.
    """
    if status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                secs = int(retry_after)
            except (TypeError, ValueError):
                secs = None
            if secs is not None and 0 < secs <= RATE_LIMIT_MAX_WAIT:
                return secs
        return RATE_LIMIT_FALLBACK_WAIT
    return BACKOFF_FACTOR ** attempt


def _get_page(base_url, headers, page, *, endpoint="/processes", extra_params=None):
    """Fetch a single page with retry logic. Returns parsed JSON or DEGRADED."""
    url = f"{base_url}{endpoint}"
    params = {"per_page": DEFAULT_PER_PAGE, "page": page}
    if extra_params:
        params.update(extra_params)

    if page > 1:
        time.sleep(INTER_PAGE_PAUSE)

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
            wait_s = _retry_wait_seconds(resp, resp.status_code, attempt)
            logger.warning(
                "leadsimple_retryable_error",
                extra={
                    "page": page,
                    "attempt": attempt,
                    "status_code": resp.status_code,
                    "wait_seconds": wait_s,
                },
            )
            time.sleep(wait_s)
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
