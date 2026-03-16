"""Fetch subscribers from MailerLite."""
import logging
import os

import requests

logger = logging.getLogger(__name__)

MAILERLITE_BASE = "https://connect.mailerlite.com/api"


def fetch_mailerlite_subscribers():
    """
    Fetch all active MailerLite subscribers (paginated, cursor-based).
    Returns a list of dicts with at least 'email' and 'name' keys.
    Uses the new MailerLite API (v3) with Bearer token auth.
    """
    mailerlite_key = os.environ["MAILERLITE_API_KEY"]
    headers = {
        "Authorization": "Bearer %s" % mailerlite_key,
        "Content-Type": "application/json",
    }

    subscribers = []
    params = {"limit": 1000, "filter[status]": "active"}

    while True:
        resp = requests.get(
            "%s/subscribers" % MAILERLITE_BASE,
            headers=headers,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        page = body.get("data", [])
        if not page:
            break
        subscribers.extend(page)
        next_cursor = body.get("meta", {}).get("next_cursor")
        if not next_cursor:
            break
        params = {"limit": 1000, "filter[status]": "active", "cursor": next_cursor}

    logger.info("MailerLite: fetched %d subscribers", len(subscribers))
    return subscribers
