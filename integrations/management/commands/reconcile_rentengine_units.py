"""
Read-only reconciliation: RentEngine units vs RentVine listings vs core.Unit.

Does NOT create, update, or delete any database rows.
"""

import logging
from collections import Counter
from difflib import SequenceMatcher

from django.core.management.base import BaseCommand

from core.models import Unit
from integrations.rentengine.client import RentEngineClient
from integrations.rentengine.mappers import map_unit
from integrations.rentengine.services import (
    build_listing_to_unit_map,
    resolve_rentengine_units,
)
from integrations.rentvine.client import RentvineClient

logger = logging.getLogger(__name__)


def _best_address_match(target, candidates):
    """
    Return the (unit_id, address, score) of the closest core.Unit by
    address string similarity, or None if no candidates.
    """
    if not candidates or not target:
        return None
    best = None
    best_score = 0.0
    for uid, addr in candidates:
        score = SequenceMatcher(None, target.lower(), addr.lower()).ratio()
        if score > best_score:
            best_score = score
            best = (uid, addr, round(best_score, 3))
    return best


class Command(BaseCommand):
    help = (
        "Read-only reconciliation of RentEngine units against "
        "RentVine listings and core.Unit records."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Process only the first N RentEngine units (0 = all).",
        )

    def handle(self, *args, **options):
        limit = options["limit"]

        # --- Fetch RentEngine units ---
        self.stdout.write("Fetching RentEngine units...")
        re_client = RentEngineClient()
        raw_units = re_client.get_all("units")
        logger.info(
            "rentengine_units_fetched",
            extra={"count": len(raw_units)},
        )

        if limit > 0:
            raw_units = raw_units[:limit]
            self.stdout.write(f"  (limited to first {limit})")

        re_units = [map_unit(u) for u in raw_units]

        # --- Fetch RentVine listings ---
        self.stdout.write("Fetching RentVine listings...")
        rv_client = RentvineClient()
        rv_listings = rv_client.get_all("properties/listings/search")
        logger.info(
            "rentvine_listings_fetched",
            extra={"count": len(rv_listings)},
        )

        # Inspect shape of first listing row
        if rv_listings:
            first_row = rv_listings[0]
            outer_keys = sorted(first_row.keys()) if isinstance(first_row, dict) else []
            inner_obj = first_row.get("listing") or {} if isinstance(first_row, dict) else {}
            inner_keys = sorted(inner_obj.keys()) if isinstance(inner_obj, dict) else []
            self.stdout.write(f"  Listing row outer keys: {outer_keys}")
            self.stdout.write(f"  Listing row inner keys: {inner_keys}")

            if "listing" not in (first_row if isinstance(first_row, dict) else {}):
                self.stderr.write(
                    self.style.ERROR(
                        "Unexpected listing shape: no 'listing' key in outer row. "
                        "Aborting to avoid empty map."
                    )
                )
                return

        listing_to_rv_unit = build_listing_to_unit_map(rv_listings)

        # --- Load core.Unit data ---
        db_units_qs = Unit.objects.select_related("property").all()
        db_unit_count = db_units_qs.count()

        rv_id_to_db = {}
        db_addresses = []
        for u in db_units_qs:
            if u.rentvine_id is not None:
                rv_id_to_db[u.rentvine_id] = u.id
            db_addresses.append((u.id, u.display_address))

        # --- Reconcile ---
        resolution = resolve_rentengine_units(
            re_units, listing_to_rv_unit, rv_id_to_db
        )
        matched = resolution.matched
        unmatched_no_extracted = resolution.unmatched_no_extracted
        unmatched_not_parseable = resolution.unmatched_not_parseable
        unmatched_listing_missing = resolution.unmatched_listing_missing
        unmatched_unit_missing = resolution.unmatched_unit_missing

        status_counter = Counter()
        for reu in re_units:
            status_counter[reu["status"] or "(blank)"] += 1

        # --- Print report ---
        self.stdout.write("")
        self.stdout.write("=" * 72)
        self.stdout.write("RENTENGINE \u2194 RENTVINE \u2194 CORE.UNIT RECONCILIATION")
        self.stdout.write("=" * 72)

        # Totals
        self.stdout.write("")
        self.stdout.write("TOTALS")
        self.stdout.write(f"  RentEngine units fetched : {len(re_units)}")
        self.stdout.write(f"  RentVine listings fetched: {len(rv_listings)}")
        self.stdout.write(f"  core.Unit rows in DB     : {db_unit_count}")
        self.stdout.write("")

        # Matched
        self.stdout.write(f"MATCHED: {len(matched)}")
        if matched:
            self.stdout.write(
                f"  {'RE ID':>8}  {'RV Unit ID':>10}  "
                f"{'DB Unit':>8}  Address"
            )
            self.stdout.write(f"  {'-----':>8}  {'----------':>10}  "
                              f"{'-------':>8}  -------")
            for m in matched:
                self.stdout.write(
                    f"  {m['rentengine_id']:>8}  "
                    f"{m['rentvine_unit_id']:>10}  "
                    f"{m['db_unit_id']:>8}  "
                    f"{m['address']}"
                )
        self.stdout.write("")

        # Unmatched
        total_unmatched = (
            len(unmatched_no_extracted)
            + len(unmatched_not_parseable)
            + len(unmatched_listing_missing)
            + len(unmatched_unit_missing)
        )
        self.stdout.write(f"UNMATCHED: {total_unmatched}")

        def _print_unmatched_group(label, items, extra_field=None):
            if not items:
                return
            self.stdout.write(f"\n  {label} ({len(items)}):")
            for item in items:
                line = f"    RE {item['rentengine_id']:>8}  {item['formatted_address']}"
                if extra_field and extra_field in item:
                    line += f"  (rv_unit_id={item[extra_field]})"
                self.stdout.write(line)
                suggestion = _best_address_match(
                    item["formatted_address"], db_addresses
                )
                if suggestion:
                    self.stdout.write(
                        f"             \u21b3 closest DB: Unit #{suggestion[0]} "
                        f'"{suggestion[1]}" (score={suggestion[2]})'
                    )

        _print_unmatched_group(
            "No extracted_from value", unmatched_no_extracted
        )
        _print_unmatched_group(
            "extracted_from present but not parseable",
            unmatched_not_parseable,
        )
        _print_unmatched_group(
            "Listing ID not found in RentVine listing map",
            unmatched_listing_missing,
        )
        _print_unmatched_group(
            "RentVine unit ID not found in core.Unit",
            unmatched_unit_missing,
            extra_field="rv_unit_id",
        )

        # Status breakdown
        self.stdout.write("")
        self.stdout.write("STATUS BREAKDOWN:")
        for status, count in status_counter.most_common():
            self.stdout.write(f"  {status:<20} {count}")

        self.stdout.write("")
        self.stdout.write("=" * 72)

        logger.info(
            "reconciliation_complete",
            extra={
                "matched": len(matched),
                "unmatched_no_extracted": len(unmatched_no_extracted),
                "unmatched_not_parseable": len(unmatched_not_parseable),
                "unmatched_listing_missing": len(unmatched_listing_missing),
                "unmatched_unit_missing": len(unmatched_unit_missing),
            },
        )
