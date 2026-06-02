"""
Property Meld API client with OAuth2 client credentials flow, pagination, and retry.

Auth flow: POST client_id + client_secret (form-encoded) to /oauth/token/ -> Bearer token.
Token expiry is tracked; re-authenticates proactively 60s before expiry.
"""

import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class PropertyMeldAPIError(Exception):
    """Raised when the Property Meld API returns an unexpected error."""

    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class PropertyMeldClient:
    """
    HTTP client for the Property Meld REST API.

    Uses OAuth2 client credentials flow. Authenticates lazily on first request
    and re-authenticates proactively 60s before the token expires.
    """

    MAX_RETRIES = 3
    BACKOFF_FACTOR = 2  # seconds: 2, 4, 8
    DEFAULT_PAGE_SIZE = 100
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
    TOKEN_EXPIRY_BUFFER = 60  # seconds before expiry to refresh

    def __init__(
        self, base_url=None, client_id=None, client_secret=None, management_id=None
    ):
        config = getattr(settings, "PROPERTY_MELD", {})
        self.base_url = (
            base_url
            or config.get("BASE_URL", "https://api.propertymeld.com/api/v2")
        ).rstrip("/")
        self.client_id = client_id or config.get("CLIENT_ID", "")
        self.client_secret = client_secret or config.get("CLIENT_SECRET", "")
        self.management_id = management_id or config.get("MANAGEMENT_ID", "")

        if not all([self.client_id, self.client_secret]):
            raise PropertyMeldAPIError(
                "Property Meld credentials not configured. Set PROPERTY_MELD_CLIENT_ID "
                "and PROPERTY_MELD_CLIENT_SECRET environment variables."
            )
        if not self.management_id:
            raise PropertyMeldAPIError(
                "Property Meld management ID not configured. Set PROPERTY_MELD_MANAGEMENT_ID "
                "to your management company ID (found in the PM dashboard URL)."
            )

        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self._token = None
        self._token_expires_at = 0.0  # epoch seconds

    def _authenticate(self):
        """POST OAuth2 client credentials (form-encoded) and store Bearer token."""
        token_url = f"{self.base_url}/oauth/token/"
        try:
            response = self.session.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise PropertyMeldAPIError(
                f"Authentication request failed: {exc}"
            ) from exc

        if response.status_code >= 400:
            raise PropertyMeldAPIError(
                f"Authentication failed ({response.status_code}): {response.text[:500]}",
                status_code=response.status_code,
                response_body=response.text,
            )

        data = response.json()
        self._token = (
            data.get("access_token")
            or data.get("token")
            or data.get("auth_token")
        )
        if not self._token:
            raise PropertyMeldAPIError(
                "Authentication response missing token",
                response_body=response.text,
            )

        expires_in = data.get("expires_in")
        if expires_in:
            try:
                self._token_expires_at = time.time() + int(expires_in)
            except (TypeError, ValueError):
                self._token_expires_at = time.time() + 3600
        else:
            self._token_expires_at = time.time() + 3600

        logger.info("property_meld_auth_success")

    def _ensure_auth(self):
        """Authenticate if no token or token is about to expire."""
        if (
            not self._token
            or time.time() >= self._token_expires_at - self.TOKEN_EXPIRY_BUFFER
        ):
            self._authenticate()

    def _get_auth_headers(self):
        return {
            "Authorization": f"Bearer {self._token}",
            "X-Multitenant-Id": str(self.management_id),
        }

    def _request(self, method, path, params=None, json_body=None):
        """Make a single HTTP request with retry logic and 401 re-auth."""
        self._ensure_auth()
        url = f"{self.base_url}/{path.lstrip('/')}"
        reauthenticated = False

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=self._get_auth_headers(),
                    timeout=30,
                )
            except requests.RequestException as exc:
                if attempt < self.MAX_RETRIES:
                    wait = self.BACKOFF_FACTOR * (2**attempt)
                    logger.warning(
                        "property_meld_request_retry",
                        extra={
                            "attempt": attempt + 1,
                            "max_attempts": self.MAX_RETRIES + 1,
                            "wait_seconds": wait,
                            "error": str(exc),
                        },
                    )
                    time.sleep(wait)
                    continue
                raise PropertyMeldAPIError(
                    f"Request failed after retries: {exc}"
                ) from exc

            # Handle 401: re-authenticate once, then retry
            if response.status_code == 401 and not reauthenticated:
                logger.info("property_meld_reauth_on_401")
                self._authenticate()
                reauthenticated = True
                continue

            if response.status_code in self.RETRYABLE_STATUS_CODES:
                if attempt < self.MAX_RETRIES:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait = int(retry_after)
                        except ValueError:
                            wait = self.BACKOFF_FACTOR * (2**attempt)
                    else:
                        wait = self.BACKOFF_FACTOR * (2**attempt)
                    logger.warning(
                        "property_meld_api_retry",
                        extra={
                            "status_code": response.status_code,
                            "attempt": attempt + 1,
                            "max_attempts": self.MAX_RETRIES + 1,
                            "wait_seconds": wait,
                        },
                    )
                    time.sleep(wait)
                    continue
                raise PropertyMeldAPIError(
                    f"API returned {response.status_code} after {self.MAX_RETRIES + 1} attempts",
                    status_code=response.status_code,
                    response_body=response.text,
                )

            if response.status_code >= 400:
                raise PropertyMeldAPIError(
                    f"API returned {response.status_code}: {response.text[:500]}",
                    status_code=response.status_code,
                    response_body=response.text,
                )

            return response

        raise PropertyMeldAPIError("Request failed: max retries exceeded")

    def get(self, path, params=None):
        """GET request, returns parsed JSON."""
        response = self._request("GET", path, params=params)
        return response.json()

    def _extract_records(self, data):
        """
        Extract the list of records from an API response.
        Probes multiple common response shapes.
        """
        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            for key in ("results", "data", "items", "records", "melds"):
                if key in data and isinstance(data[key], list):
                    return data[key]

            list_values = [v for v in data.values() if isinstance(v, list)]
            if len(list_values) == 1:
                return list_values[0]

        return []

    def get_all(self, path, page_size=None):
        """
        Fetch all records from a paginated endpoint using limit/offset.
        Uses the `next` field (DRF standard) as the authoritative stop signal.
        Returns a flat list of all records.
        """
        page_size = page_size or self.DEFAULT_PAGE_SIZE
        all_records = []
        offset = 0

        while True:
            params = {"limit": page_size, "offset": offset}
            data = self.get(path, params=params)
            records = self._extract_records(data)

            if not records:
                break

            all_records.extend(records)
            logger.info(
                "property_meld_fetch_page",
                extra={
                    "offset": offset,
                    "path": path,
                    "page_count": len(records),
                    "total_so_far": len(all_records),
                },
            )

            # DRF: `next` is null/absent when there are no more pages
            if isinstance(data, dict) and "next" in data:
                if data["next"] is None:
                    break

            if len(records) < page_size:
                break

            offset += page_size

        logger.info(
            "property_meld_fetch_complete",
            extra={"total_records": len(all_records), "path": path},
        )
        return all_records
