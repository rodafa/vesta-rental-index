"""
Management command: check_new_listings

Polls RentEngine for active unit listings, deduplicates against KnownListing
(180-day window), then for each new listing:
  1. Sends a listing announcement to all MailerLite subscribers via SendGrid
  2. Sends a personalized $200-referral email to all active tenants

Test mode:
  --test-email you@example.com
  Sends both emails to a single address using the first active RentEngine unit
  as a dummy listing. Does not write to KnownListing or deduplicate.
"""
import logging
import os

import requests
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

SENDGRID_BASE = "https://api.sendgrid.com/v3"


def _send_test_email(email, listing):
    """Send both email types to a single address for QA purposes."""
    from leasing.services.listing_alerts import (
        _announcement_html,
        _build_listing_context,
        _referral_html,
    )

    sendgrid_key = os.environ["SENDGRID_API_KEY"]
    from_name = os.environ.get("SENDGRID_FROM_NAME", "Vesta Property Management")
    announcement_from = os.environ.get("SENDGRID_FROM_EMAIL_ANNOUNCEMENTS", "hello@vestapm.com")
    referral_from = os.environ.get("SENDGRID_FROM_EMAIL_REFERRALS", "support@vestapm.com")

    ctx = _build_listing_context(listing)

    headers = {
        "Authorization": "Bearer %s" % sendgrid_key,
        "Content-Type": "application/json",
    }
    to = [{"email": email}]

    for subject, html_body, from_email in [
        (
            "[TEST] New Listing: %s — $%s/mo" % (ctx["formatted_address"], ctx["rent"]),
            _announcement_html(ctx),
            announcement_from,
        ),
        (
            "[TEST] Know someone looking for a home? Earn $200 off rent.",
            _referral_html(ctx).replace("-first_name-", "Rodrigo"),
            referral_from,
        ),
    ]:
        resp = requests.post(
            "%s/mail/send" % SENDGRID_BASE,
            headers=headers,
            json={
                "personalizations": [{"to": to}],
                "from": {"email": from_email, "name": from_name},
                "subject": subject,
                "content": [{"type": "text/html", "value": html_body}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        logger.info("Test email sent to %s: %s", email, subject)


class Command(BaseCommand):
    help = "Check RentEngine for new listings and send alert emails."

    def add_arguments(self, parser):
        parser.add_argument(
            "--test-email",
            metavar="EMAIL",
            help="Send both email templates to this address using a dummy listing. "
                 "Does not write to KnownListing.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            metavar="N",
            help="Only process up to N new listings per run (0 = no limit).",
        )

    def handle(self, *args, **options):
        from datetime import timedelta

        from django.utils import timezone

        from integrations.rentengine.client import RentEngineClient
        from leasing.models import KnownListing
        from leasing.services.listing_alerts import (
            send_listing_announcement,
            send_tenant_referral_blast,
        )

        test_email = options.get("test_email")

        if test_email:
            # Pull the first active unit from RentEngine as a realistic dummy
            client = RentEngineClient()
            units = client.get_all("/units")
            active = [u for u in units if (u.get("status") or "").lower() == "active"]
            dummy = active[0] if active else {"address": "TEST LISTING — 123 Main St"}
            _send_test_email(test_email, dummy)
            self.stdout.write("Test emails sent to %s" % test_email)
            return

        client = RentEngineClient()
        units = client.get_all("/units")

        # Filter to active/vacant units only
        listings = [u for u in units if (u.get("status") or "").lower() == "active"]
        logger.info("check_new_listings: %d active units from RentEngine", len(listings))

        new_count = 0
        announcement_count = 0
        referral_count = 0
        cutoff = timezone.now() - timedelta(days=180)
        limit = options.get("limit") or 0

        for listing in listings:
            if limit and new_count >= limit:
                break
            listing_id = str(listing.get("id", ""))
            if not listing_id:
                continue

            # 180-day deduplication: skip if already alerted within 180 days
            if KnownListing.objects.filter(
                listing_id=listing_id, first_seen_at__gte=cutoff
            ).exists():
                continue

            new_count += 1
            address = listing.get("address", "")
            logger.info("check_new_listings: new listing %s — %s", listing_id, address)

            ann_sent = send_listing_announcement(listing)

            ref_sent = send_tenant_referral_blast(listing)

            KnownListing.objects.create(
                listing_id=listing_id,
                address=address,
                announcement_sent_at=timezone.now() if ann_sent else None,
                referral_sent_at=timezone.now() if ref_sent else None,
            )

            if ann_sent:
                announcement_count += 1
            if ref_sent:
                referral_count += 1

        logger.info(
            "check_new_listings: %d new listings, %d announcements sent, %d referral blasts sent",
            new_count,
            announcement_count,
            referral_count,
        )
        self.stdout.write(
            "Done: %d new listings, %d announcements, %d referral blasts"
            % (new_count, announcement_count, referral_count)
        )
