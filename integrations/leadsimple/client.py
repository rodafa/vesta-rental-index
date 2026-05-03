import logging
from datetime import date, timedelta

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class LeadSimpleClient:
    """Thin client for the LeadSimple REST API (base: api.leadsimple.com/rest)."""

    def __init__(self):
        cfg = getattr(settings, "LEADSIMPLE", {})
        self.base_url = cfg.get("BASE_URL") or "https://api.leadsimple.com/rest"
        self.base_url = self.base_url.rstrip("/")
        self.api_key = cfg.get("API_KEY", "")

    def _get(self, path, **params):
        url = f"{self.base_url}{path}"
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_active_processes(self) -> list:
        """Return open (not closed) processes created within the last 90 days.

        Each dict:
          name       – process name (e.g. "06 Lease Renewal for 26 Reid St")
          stage_name – current stage name
          created_at – ISO date string YYYY-MM-DD
          comments   – comment text truncated to 200 chars
          address    – first property address (e.g. "26 Reid St")
        """
        if not self.api_key:
            logger.debug("LeadSimple: API_KEY not configured — skipping process context")
            return []

        cutoff = date.today() - timedelta(days=90)
        results = []
        page = 1

        while True:
            try:
                data = self._get("/processes", per_page=100, page=page)
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status == 404:
                    logger.debug("LeadSimple: /processes endpoint not found (404)")
                else:
                    logger.warning("LeadSimple: failed to fetch processes (page %s)", page, exc_info=True)
                break

            items = data.get("data") or []
            meta = data.get("meta") or {}

            if not items:
                break

            for process in items:
                # Skip closed processes
                if process.get("closed_at"):
                    continue

                # Filter by created_at >= 90-day cutoff
                raw_created = process.get("created_at") or ""
                try:
                    created_date = date.fromisoformat(raw_created[:10])
                except (ValueError, TypeError):
                    created_date = None

                if created_date and created_date < cutoff:
                    continue

                # Stage name
                stage = process.get("stage") or {}
                stage_name = stage.get("name") or ""

                # Comments (usually null on processes)
                comments_raw = process.get("comments") or ""
                if isinstance(comments_raw, list):
                    comments_raw = " ".join(
                        (c.get("body") or c.get("text") or "") for c in comments_raw
                    )
                comments = str(comments_raw)[:200]

                # Address from linked properties
                props = process.get("properties") or []
                address = props[0].get("address", "") if props else ""

                results.append({
                    "name": process.get("name") or "",
                    "stage_name": stage_name,
                    "created_at": created_date.isoformat() if created_date else raw_created[:10],
                    "comments": comments,
                    "address": address,
                })

            # Pagination via meta
            total_pages = meta.get("total_pages") or 1
            if page >= total_pages:
                break
            page += 1

        logger.info("LeadSimple: fetched %d active processes", len(results))
        return results
