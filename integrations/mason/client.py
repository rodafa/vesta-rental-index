"""
Mason External API client with Bearer auth, cursor pagination, and retry.

Base URL: https://mason.enduring-labs.com/api/external/v3
Auth: Authorization: Bearer <MASON_API_KEY>
Pagination: follow pagination.nextCursor until pagination.hasMore is false.

Query parameters verified against the Mason External API v3 spec:

  GET /work-orders        — status, updatedAfter (ISO 8601 timestamp w/ tz)
  GET /visits             — logDateAfter (ISO 8601 timestamp w/ tz),
                            masonWorkOrderId
  GET /work-order-activity-logs — after, before (ISO 8601 timestamp w/ tz)
  GET /contacts           — ids (comma-separated contact IDs)

All endpoints accept cursor for pagination.
"""

import logging
import re
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class MasonAPIError(Exception):
    """Raised when the Mason API returns an unexpected error."""

    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class MasonClient:
    """
    HTTP client for the Mason External API (v3).

    Uses Bearer token auth.  Handles cursor-based pagination and retries
    with exponential backoff on 429/5xx.
    """

    BASE_URL = "https://mason.enduring-labs.com/api/external/v3"
    MAX_RETRIES = 3
    BACKOFF_FACTOR = 2  # seconds: 2, 4, 8
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

    # No documented cap on the ids parameter (results paginate normally,
    # page size 1-100).  Constraint is URL length — keep chunks short so
    # the query string stays well under typical server limits.
    CONTACTS_CHUNK_SIZE = 50

    def __init__(self, api_key=None):
        self.api_key = api_key or getattr(settings, "MASON_API_KEY", "")
        if not self.api_key:
            raise MasonAPIError(
                "Mason API key not configured. Set MASON_API_KEY environment variable."
            )
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        })

    def _request(self, method, path, params=None):
        """Make a single HTTP request with retry logic."""
        url = f"{self.BASE_URL}/{path.lstrip('/')}"

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = self.session.request(
                    method, url, params=params, timeout=30,
                )
            except requests.RequestException as exc:
                if attempt < self.MAX_RETRIES:
                    wait = self.BACKOFF_FACTOR * (2 ** attempt)
                    logger.warning(
                        "mason_request_failed",
                        extra={
                            "attempt": attempt + 1,
                            "max_retries": self.MAX_RETRIES + 1,
                            "wait_seconds": wait,
                            "error": str(exc),
                            "path": path,
                        },
                    )
                    time.sleep(wait)
                    continue
                raise MasonAPIError(
                    f"Request failed after retries: {exc}"
                ) from exc

            if response.status_code in self.RETRYABLE_STATUS_CODES:
                if attempt < self.MAX_RETRIES:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait = int(retry_after)
                        except ValueError:
                            wait = self.BACKOFF_FACTOR * (2 ** attempt)
                    else:
                        wait = self.BACKOFF_FACTOR * (2 ** attempt)
                    logger.warning(
                        "mason_api_retryable",
                        extra={
                            "status_code": response.status_code,
                            "attempt": attempt + 1,
                            "wait_seconds": wait,
                            "path": path,
                        },
                    )
                    time.sleep(wait)
                    continue
                raise MasonAPIError(
                    f"API returned {response.status_code} after "
                    f"{self.MAX_RETRIES + 1} attempts",
                    status_code=response.status_code,
                    response_body=response.text,
                )

            if response.status_code >= 400:
                raise MasonAPIError(
                    f"API returned {response.status_code}: "
                    f"{response.text[:500]}",
                    status_code=response.status_code,
                    response_body=response.text,
                )

            return response

        raise MasonAPIError("Request failed: max retries exceeded")

    def get(self, path, params=None):
        """GET request, returns parsed JSON."""
        response = self._request("GET", path, params=params)
        return response.json()

    def get_paginated(self, path, params=None):
        """
        Fetch all records from a cursor-paginated endpoint.

        Follows pagination.nextCursor until pagination.hasMore is false.
        Returns a flat list of all records from the ``data`` key.
        """
        params = dict(params or {})
        all_records = []

        while True:
            data = self.get(path, params=params)

            records = data.get("data", [])
            if isinstance(records, list):
                all_records.extend(records)

            pagination = data.get("pagination", {})
            logger.info(
                "mason_page_fetched",
                extra={
                    "path": path,
                    "page_records": len(records),
                    "total_so_far": len(all_records),
                    "has_more": pagination.get("hasMore", False),
                },
            )

            if not pagination.get("hasMore", False):
                break

            next_cursor = pagination.get("nextCursor")
            if not next_cursor:
                break
            params["cursor"] = next_cursor

        logger.info(
            "mason_fetch_complete",
            extra={"path": path, "total_records": len(all_records)},
        )
        return all_records

    # ------------------------------------------------------------------
    # Date-filter guard
    # ------------------------------------------------------------------

    # Matches a trailing timezone offset: Z, +00:00, -05:00, etc.
    _TZ_OFFSET_RE = re.compile(r"(Z|[+-]\d{2}:\d{2})$")

    def _require_tz(self, value, param_name):
        """Raise if a date-filter value lacks a timezone offset.

        The Mason spec requires ISO 8601 timestamps with a timezone.  An
        invalid date silently matches zero records — no error, no log.
        This guard makes the failure loud and immediate.
        """
        if not self._TZ_OFFSET_RE.search(value):
            raise MasonAPIError(
                f"Date filter '{param_name}' must include a timezone offset "
                f"(e.g. 'Z' or '+00:00'), got: {value!r}"
            )

    # ------------------------------------------------------------------
    # Domain-specific fetch methods
    # ------------------------------------------------------------------

    def get_work_orders(self, status=None, updated_after=None):
        """
        GET /work-orders with optional status and updatedAfter filters.

        status: single status string (e.g. "created") or None.
        updated_after: ISO 8601 timestamp with timezone offset, or None.
        """
        params = {}
        if status:
            params["status"] = status
        if updated_after:
            self._require_tz(updated_after, "updatedAfter")
            params["updatedAfter"] = updated_after
        return self.get_paginated("work-orders", params)

    def get_visits(self, log_date_after=None, work_order_id=None):
        """GET /visits with optional filters.

        log_date_after: ISO 8601 timestamp with timezone offset, or None.
        work_order_id: single Mason work-order ID — returns visits for that WO.
        """
        params = {}
        if log_date_after:
            self._require_tz(log_date_after, "logDateAfter")
            params["logDateAfter"] = log_date_after
        if work_order_id:
            params["masonWorkOrderId"] = work_order_id
        return self.get_paginated("visits", params)

    def get_activity_logs(self, after=None):
        """GET /work-order-activity-logs with optional after filter.

        after: ISO 8601 timestamp with timezone offset, or None.
        """
        params = {}
        if after:
            self._require_tz(after, "after")
            params["after"] = after
        return self.get_paginated("work-order-activity-logs", params)

    def get_estimates(self, updated_after=None):
        """GET /estimates with optional updatedAfter filter.

        updated_after: ISO 8601 timestamp with timezone offset, or None.
        """
        params = {}
        if updated_after:
            self._require_tz(updated_after, "updatedAfter")
            params["updatedAfter"] = updated_after
        return self.get_paginated("estimates", params)

    def get_contacts(self, contact_ids):
        """
        GET /contacts with ids filter, chunked to CONTACTS_CHUNK_SIZE.

        contact_ids: iterable of contact ID strings/ints.
        Returns a flat list of all contact records across all chunks.
        """
        ids_list = list(set(str(cid) for cid in contact_ids if cid))
        if not ids_list:
            return []

        all_contacts = []
        for i in range(0, len(ids_list), self.CONTACTS_CHUNK_SIZE):
            chunk = ids_list[i : i + self.CONTACTS_CHUNK_SIZE]
            params = {"ids": ",".join(chunk)}
            records = self.get_paginated("contacts", params)
            all_contacts.extend(records)

        return all_contacts
