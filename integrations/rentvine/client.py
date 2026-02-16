"""
Rentvine API client with HTTP Basic Auth, pagination, and retry with backoff.
"""

import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class RentvineAPIError(Exception):
    """Raised when the Rentvine API returns an unexpected error."""

    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class RentvineClient:
    """
    HTTP client for the Rentvine REST API.

    Uses HTTP Basic Auth (API key as username, API secret as password).
    Handles pagination and retries with exponential backoff on 429/5xx.
    """

    MAX_RETRIES = 3
    BACKOFF_FACTOR = 2  # seconds: 2, 4, 8
    DEFAULT_PAGE_SIZE = 100
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

    def __init__(self, subdomain=None, api_key=None, api_secret=None):
        config = getattr(settings, "RENTVINE", {})
        self.subdomain = subdomain or config.get("SUBDOMAIN", "")
        self.api_key = api_key or config.get("API_KEY", "")
        self.api_secret = api_secret or config.get("API_SECRET", "")

        if not all([self.subdomain, self.api_key, self.api_secret]):
            raise RentvineAPIError(
                "Rentvine credentials not configured. Set RENTVINE_SUBDOMAIN, "
                "RENTVINE_API_KEY, and RENTVINE_API_SECRET environment variables."
            )

        # Normalize subdomain: strip protocol/domain if user provided full URL
        sd = self.subdomain.strip()
        sd = sd.replace("https://", "").replace("http://", "")
        sd = sd.split(".rentvine.com")[0]  # "vestapm.rentvine.com/..." -> "vestapm"
        sd = sd.rstrip("/")
        self.subdomain = sd

        self.base_url = f"https://{self.subdomain}.rentvine.com/api/manager"
        self.session = requests.Session()
        self.session.auth = (self.api_key, self.api_secret)
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
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
                        "Rentvine request failed (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1, self.MAX_RETRIES + 1, wait, exc,
                    )
                    time.sleep(wait)
                    continue
                raise RentvineAPIError(f"Request failed after retries: {exc}") from exc

            if response.status_code in self.RETRYABLE_STATUS_CODES:
                if attempt < self.MAX_RETRIES:
                    # Respect Retry-After header if present
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait = int(retry_after)
                        except ValueError:
                            wait = self.BACKOFF_FACTOR * (2 ** attempt)
                    else:
                        wait = self.BACKOFF_FACTOR * (2 ** attempt)
                    logger.warning(
                        "Rentvine API %d (attempt %d/%d), retrying in %ds",
                        response.status_code, attempt + 1,
                        self.MAX_RETRIES + 1, wait,
                    )
                    time.sleep(wait)
                    continue
                raise RentvineAPIError(
                    f"API returned {response.status_code} after {self.MAX_RETRIES + 1} attempts",
                    status_code=response.status_code,
                    response_body=response.text,
                )

            if response.status_code >= 400:
                raise RentvineAPIError(
                    f"API returned {response.status_code}: {response.text[:500]}",
                    status_code=response.status_code,
                    response_body=response.text,
                )

            return response

        # Should not reach here, but just in case
        raise RentvineAPIError("Request failed: max retries exceeded")

    def get(self, path, params=None):
        """GET request, returns parsed JSON."""
        response = self._request("GET", path, params=params)
        return response.json()

    def _extract_records(self, data):
        """
        Extract the list of records from an API response.
        Probes multiple response shapes since we don't know
        the exact Rentvine schema.
        """
        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            # Common patterns: {"data": [...]}, {"results": [...]}, {"items": [...]}
            for key in ("data", "results", "items", "records"):
                if key in data and isinstance(data[key], list):
                    return data[key]

            # If the dict has a single key whose value is a list, use that
            list_values = [
                v for v in data.values() if isinstance(v, list)
            ]
            if len(list_values) == 1:
                return list_values[0]

        return []

    def _get_next_page_params(self, data, current_page, page_size):
        """
        Determine pagination parameters for the next page.
        Returns updated params dict or None if no more pages.
        """
        if isinstance(data, dict):
            # Check for explicit pagination metadata
            meta = data.get("meta", data.get("pagination", data))
            total = meta.get("total", meta.get("totalCount", meta.get("total_count")))
            if total is not None:
                try:
                    total = int(total)
                except (ValueError, TypeError):
                    total = None
                if total is not None and current_page * page_size >= total:
                    return None

            # Check for next page URL or flag
            if meta.get("next") is None and "next" in meta:
                return None
            if meta.get("hasMore") is False or meta.get("has_more") is False:
                return None

        return {"page": current_page + 1, "pageSize": page_size}

    def get_all(self, path, page_size=None):
        """
        Fetch all records from a paginated endpoint.
        Returns a flat list of all records across all pages.
        """
        page_size = page_size or self.DEFAULT_PAGE_SIZE
        all_records = []
        page = 1

        while True:
            params = {"page": page, "pageSize": page_size}
            data = self.get(path, params=params)
            records = self._extract_records(data)

            if not records:
                break

            all_records.extend(records)
            logger.info(
                "Fetched page %d from %s (%d records, %d total so far)",
                page, path, len(records), len(all_records),
            )

            # If we got fewer than page_size, we're on the last page
            if len(records) < page_size:
                break

            next_params = self._get_next_page_params(data, page, page_size)
            if next_params is None:
                break

            page = next_params["page"]

        logger.info("Fetched %d total records from %s", len(all_records), path)
        return all_records
