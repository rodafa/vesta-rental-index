"""
RentEngine API client with Bearer JWT auth, 0-indexed pagination, and retry with backoff.

API docs: https://docs.rentengine.io
Base URL: https://app.rentengine.io/api/public/v1
Rate limit: 20 req/5s, returns 429 with Retry-After header
"""

import logging
import time

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

    Uses Bearer JWT authentication.
    Handles 0-indexed pagination and retries with exponential backoff on 429/5xx.
    """

    MAX_RETRIES = 3
    BACKOFF_FACTOR = 2  # seconds: 2, 4, 8
    DEFAULT_PAGE_SIZE = 100
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

    def __init__(self, api_token=None, base_url=None):
        config = getattr(settings, "RENTENGINE", {})
        self.api_token = api_token or config.get("API_TOKEN", "")
        self.base_url = (
            base_url or config.get("BASE_URL", "")
        ).rstrip("/")

        if not self.api_token:
            raise RentEngineAPIError(
                "RentEngine credentials not configured. "
                "Set RENTENGINE_API_TOKEN environment variable."
            )
        if not self.base_url:
            raise RentEngineAPIError(
                "RentEngine base URL not configured. "
                "Set RENTENGINE_BASE_URL environment variable."
            )

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
        })

    def _request(self, method, path, params=None):
        """Make a single HTTP request with retry logic."""
        url = f"{self.base_url}/{path.lstrip('/')}"

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = self.session.request(
                    method, url, params=params, timeout=30
                )
            except requests.RequestException as exc:
                if attempt < self.MAX_RETRIES:
                    wait = self.BACKOFF_FACTOR * (2 ** attempt)
                    logger.warning(
                        "RentEngine request failed (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1, self.MAX_RETRIES + 1, wait, exc,
                    )
                    time.sleep(wait)
                    continue
                raise RentEngineAPIError(f"Request failed after retries: {exc}") from exc

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
                        "RentEngine API %d (attempt %d/%d), retrying in %ds",
                        response.status_code, attempt + 1,
                        self.MAX_RETRIES + 1, wait,
                    )
                    time.sleep(wait)
                    continue
                raise RentEngineAPIError(
                    f"API returned {response.status_code} after {self.MAX_RETRIES + 1} attempts",
                    status_code=response.status_code,
                    response_body=response.text,
                )

            if response.status_code >= 400:
                raise RentEngineAPIError(
                    f"API returned {response.status_code}: {response.text[:500]}",
                    status_code=response.status_code,
                    response_body=response.text,
                )

            return response

        # Should not reach here, but just in case
        raise RentEngineAPIError("Request failed: max retries exceeded")

    def get(self, path, params=None):
        """GET request, returns parsed JSON."""
        response = self._request("GET", path, params=params)
        return response.json() if response.text else {}

    def _extract_records(self, data):
        """
        Extract the list of records from an API response.
        Probes multiple response shapes.
        """
        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            for key in ("data", "results", "items", "units", "records"):
                if key in data and isinstance(data[key], list):
                    return data[key]

            # If the dict has a single key whose value is a list, use that
            list_values = [
                v for v in data.values() if isinstance(v, list)
            ]
            if len(list_values) == 1:
                return list_values[0]

        return []

    def get_all(self, path, page_size=None, params=None):
        """
        Fetch all records from a paginated endpoint.
        Uses 0-indexed pagination (page_number=0, limit=100).
        Returns a flat list of all records across all pages.
        """
        page_size = page_size or self.DEFAULT_PAGE_SIZE
        all_records = []
        page_number = 0
        base_params = params or {}

        while True:
            page_params = {
                **base_params,
                "limit": page_size,
                "page_number": page_number,
            }
            data = self.get(path, params=page_params)
            records = self._extract_records(data)

            if not records:
                break

            all_records.extend(records)
            logger.info(
                "Fetched page %d from %s (%d records, %d total so far)",
                page_number, path, len(records), len(all_records),
            )

            # If we got fewer than page_size, we're on the last page
            if len(records) < page_size:
                break

            page_number += 1

        logger.info("Fetched %d total records from %s", len(all_records), path)
        return all_records
