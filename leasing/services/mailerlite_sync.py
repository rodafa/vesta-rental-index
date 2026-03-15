"""Fetch subscribers from MailerLite."""
import logging
import os

import requests

logger = logging.getLogger(__name__)

MAILERLITE_BASE = "https://api.mailerlite.com/api/v2"


def fetch_mailerlite_subscribers():
    """
    Fetch all active MailerLite subscribers (paginated).
    Returns a list of dicts with at least 'email' and 'name' keys.
    """
    mailerlite_key = os.environ["MAILERLITE_API_KEY"]

    subscribers = []
    limit = 1000
    offset = 0
    while True:
        resp = requests.get(
            "%s/subscribers" % MAILERLITE_BASE,
            headers={"X-MailerLite-ApiKey": mailerlite_key},
            params={"limit": limit, "offset": offset},
            timeout=30,
        )
        resp.raise_for_status()
        page = resp.json()
        if not page:
            break
        subscribers.extend(page)
        if len(page) < limit:
            break
        offset += limit

    logger.info("MailerLite: fetched %d subscribers", len(subscribers))
    return subscribers
