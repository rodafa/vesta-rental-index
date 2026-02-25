"""
Parse Portfolio.raw_data.portfolio.contacts to link Owners to Portfolios.

RentVine's owner API doesn't return portfolioIDs, but the portfolio API
stores contacts as a JSON string in raw_data.portfolio.contacts:
  [{"contactID": "1084", "contactTypeID": "1", ...}, ...]

contactTypeID=1 = Owner. We match contactID to Owner.rentvine_contact_id.
"""

import json
import logging

from django.core.management.base import BaseCommand

from properties.models import Owner, Portfolio

logger = logging.getLogger(__name__)


def link_owners_from_portfolio_contacts():
    """Parse portfolio contacts and link owners. Returns counts."""
    linked = 0
    skipped = 0

    for portfolio in Portfolio.objects.all():
        contacts_raw = (
            portfolio.raw_data.get("portfolio", {}).get("contacts")
        )
        if not contacts_raw:
            continue

        # contacts may be a JSON string or already a list
        if isinstance(contacts_raw, str):
            try:
                contacts = json.loads(contacts_raw)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "Portfolio %s: invalid contacts JSON", portfolio.rentvine_id
                )
                continue
        else:
            contacts = contacts_raw

        if not isinstance(contacts, list):
            continue

        for contact in contacts:
            contact_id_str = contact.get("contactID")
            if not contact_id_str:
                continue

            try:
                contact_id = int(contact_id_str)
            except (ValueError, TypeError):
                continue

            try:
                owner = Owner.objects.get(rentvine_contact_id=contact_id)
                owner.portfolios.add(portfolio)
                linked += 1
            except Owner.DoesNotExist:
                skipped += 1
                logger.debug(
                    "Owner contact %d not found for portfolio %s",
                    contact_id,
                    portfolio.rentvine_id,
                )

    return {"linked": linked, "skipped": skipped}


class Command(BaseCommand):
    help = "Link Owner ↔ Portfolio M2M from portfolio raw_data contacts"

    def handle(self, *args, **options):
        result = link_owners_from_portfolio_contacts()
        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {result['linked']} links created, "
                f"{result['skipped']} contacts skipped (owner not found)"
            )
        )
