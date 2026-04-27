import logging
from datetime import date, timedelta

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class LeadSimpleClient:
    """Thin client for the LeadSimple v1 API."""

    def __init__(self):
        cfg = getattr(settings, "LEADSIMPLE", {})
        self.base_url = cfg.get("BASE_URL", "https://api.leadsimple.com/v1").rstrip("/")
        self.api_key = cfg.get("API_KEY", "")
        self.pipeline_id = cfg.get("PIPELINE_ID", "")

    def _get(self, path, **params):
        url = f"{self.base_url}{path}"
        resp = requests.get(
            url,
            auth=(self.api_key, ""),
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_active_deals(self) -> list:
        """Return deals where stage.status == 'active' and created within the last 90 days.

        Each dict: {name, stage_name, created_at (date str YYYY-MM-DD), comments (str ≤200 chars)}
        """
        if not self.api_key or not self.pipeline_id:
            logger.warning("LeadSimple: API_KEY or PIPELINE_ID not configured")
            return []

        cutoff = date.today() - timedelta(days=90)
        results = []
        page = 1

        while True:
            try:
                data = self._get(
                    f"/pipelines/{self.pipeline_id}/deals",
                    per_page=100,
                    page=page,
                )
            except Exception:
                logger.exception("LeadSimple: failed to fetch deals (page %s)", page)
                break

            # API may return a list directly or a dict with a 'data' key
            if isinstance(data, list):
                items = data
            else:
                items = data.get("data") or []

            if not items:
                break

            for deal in items:
                # Filter by stage status
                stage = deal.get("stage") or {}
                if stage.get("status") != "active":
                    continue

                # Filter by created_at >= 90-day cutoff
                raw_created = deal.get("created_at") or ""
                try:
                    created_date = date.fromisoformat(raw_created[:10])
                except (ValueError, TypeError):
                    created_date = None

                if created_date and created_date < cutoff:
                    continue

                # Extract comments (truncated to 200 chars)
                comments_raw = deal.get("comments") or deal.get("notes") or ""
                if isinstance(comments_raw, list):
                    # Some endpoints return comments as a list of objects
                    comments_raw = " ".join(
                        (c.get("body") or c.get("text") or "") for c in comments_raw
                    )
                comments = str(comments_raw)[:200]

                results.append({
                    "name": deal.get("name") or deal.get("title") or "",
                    "stage_name": stage.get("name") or stage.get("label") or "",
                    "created_at": created_date.isoformat() if created_date else raw_created[:10],
                    "comments": comments,
                })

            # Check for next page
            if isinstance(data, dict) and not data.get("next"):
                break
            if isinstance(data, list) and len(data) < 100:
                break

            page += 1

        logger.info("LeadSimple: fetched %d active deals", len(results))
        return results
