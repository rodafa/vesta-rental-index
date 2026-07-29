"""
RentEngine API client with Bearer auth, rate limiting, and retry with backoff.
"""

import logging
import time
from collections import deque

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class RentEngineAPIError(Exception):
    """Raised when the RentEngine API returns an unexpected error."""

    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class RentEngineClient:
    """
    HTTP client for the RentEngine REST API.

    Uses Bearer token auth.
    Handles pagination (0-indexed page_number, bare-array responses)
    and retries with exponential backoff on 429/5xx.
    Proactive rate limiting: max 30 requests per 5-second sliding window.
    """

    MAX_RETRIES = 3
    BACKOFF_FACTOR = 2  # seconds: 2, 4, 8
    DEFAULT_PAGE_SIZE = 100
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

    # Rate limit: 30 requests per 5-second sliding window
    RATE_LIMIT_MAX = 30
    RATE_LIMIT_WINDOW = 5.0  # seconds

    def __init__(self, api_token=None, base_url=None):
        config = getattr(settings, "RENTENGINE", {})
        self.api_token = api_token or config.get("API_TOKEN", "")
        self.base_url = base_url or config.get("BASE_URL", "")

        if not self.api_token:
            raise RentEngineAPIError(
                "RentEngine credentials not configured. "
                "Set RENTENGINE_API_TOKEN environment variable."
            )
        if not self.base_url:
            raise RentEngineAPIError(
                "RentEngine base URL not configured. "
                "Set RENTENGINE_API_URL environment variable."
            )

        self.base_url = self.base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
        })

        # Sliding-window timestamps for proactive rate limiting
        self._request_timestamps = deque()

    def _wait_for_rate_limit(self):
        """Block until we have capacity in the sliding window."""
        now = time.monotonic()
        # Evict timestamps older than the window
        while self._request_timestamps and (
            now - self._request_timestamps[0] >= self.RATE_LIMIT_WINDOW
        ):
            self._request_timestamps.popleft()

        if len(self._request_timestamps) >= self.RATE_LIMIT_MAX:
            oldest = self._request_timestamps[0]
            wait = self.RATE_LIMIT_WINDOW - (now - oldest)
            if wait > 0:
                logger.info(
                    "rate_limit_wait",
                    extra={"wait_seconds": round(wait, 2)},
                )
                time.sleep(wait)
            # Evict again after sleeping
            now = time.monotonic()
            while self._request_timestamps and (
                now - self._request_timestamps[0] >= self.RATE_LIMIT_WINDOW
            ):
                self._request_timestamps.popleft()

        self._request_timestamps.append(time.monotonic())

    def _request(self, method, path, params=None):
        """Make a single HTTP request with proactive rate limiting and retry logic."""
        url = f"{self.base_url}/{path.lstrip('/')}"

        for attempt in range(self.MAX_RETRIES + 1):
            self._wait_for_rate_limit()

            try:
                response = self.session.request(
                    method, url, params=params, timeout=30
                )
            except requests.RequestException as exc:
                if attempt < self.MAX_RETRIES:
                    wait = self.BACKOFF_FACTOR * (2 ** attempt)
                    logger.warning(
                        "rentengine_request_error",
                        extra={
                            "attempt": attempt + 1,
                            "max_attempts": self.MAX_RETRIES + 1,
                            "wait_seconds": wait,
                            "error": str(exc),
                        },
                    )
                    time.sleep(wait)
                    continue
                raise RentEngineAPIError(
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
                        "rentengine_retryable_status",
                        extra={
                            "status_code": response.status_code,
                            "attempt": attempt + 1,
                            "max_attempts": self.MAX_RETRIES + 1,
                            "wait_seconds": wait,
                        },
                    )
                    time.sleep(wait)
                    continue
                raise RentEngineAPIError(
                    f"API returned {response.status_code} after "
                    f"{self.MAX_RETRIES + 1} attempts",
                    status_code=response.status_code,
                    response_body=response.text,
                )

            if response.status_code >= 400:
                raise RentEngineAPIError(
                    f"API returned {response.status_code}: "
                    f"{response.text[:500]}",
                    status_code=response.status_code,
                    response_body=response.text,
                )

            return response

        raise RentEngineAPIError("Request failed: max retries exceeded")

    def get(self, path, params=None):
        """GET request, returns parsed JSON."""
        response = self._request("GET", path, params=params)
        return response.json()

    def get_all(self, path, page_size=None):
        """
        Fetch all records from a paginated endpoint.

        RentEngine uses 0-indexed page_number and returns bare JSON arrays
        (no envelope). Stops when a page returns fewer than `limit` items.
        """
        page_size = page_size or self.DEFAULT_PAGE_SIZE
        all_records = []
        page_number = 0

        while True:
            params = {"limit": page_size, "page_number": page_number}
            data = self.get(path, params=params)

            if not isinstance(data, list):
                logger.warning(
                    "rentengine_unexpected_response_type",
                    extra={"path": path, "type": type(data).__name__},
                )
                break

            all_records.extend(data)
            logger.info(
                "rentengine_page_fetched",
                extra={
                    "path": path,
                    "page_number": page_number,
                    "page_records": len(data),
                    "total_so_far": len(all_records),
                },
            )

            if len(data) < page_size:
                break

            page_number += 1

        logger.info(
            "rentengine_fetch_complete",
            extra={"path": path, "total_records": len(all_records)},
        )
        return all_records
