"""
Market comps reducer for weekly leasing snapshots.

Pure function — no DB, no network, fully testable.

NAMING RULE (owner-facing context):
  Never say "rented."  Use "came off the market" / "off_market".
  Owners read these notes; "rented" implies the PM has inside knowledge
  about lease execution we do not have.
"""

import logging
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from statistics import median

logger = logging.getLogger(__name__)

OFF_MARKET_LOOKBACK_DAYS = 90

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SUFFIX_MAP = {
    "avenue": "ave", "street": "st", "road": "rd", "drive": "dr",
    "lane": "ln", "court": "ct", "circle": "cir", "boulevard": "blvd",
    "place": "pl", "terrace": "ter", "highway": "hwy", "trail": "trl",
    "parkway": "pkwy",
}

# Matches trailing unit designators: #3, apt 3, unit b, ste 2
_UNIT_RE = re.compile(
    r"\s+(?:#|apt\.?|unit\.?|ste\.?)\s*\S+$", re.IGNORECASE,
)


def _normalize_address(raw):
    """
    Normalize a street address for comparison.

    1. Drop everything after the first comma (city, state, zip).
    2. Lowercase.
    3. Strip trailing periods.
    4. Strip trailing unit designators (#3, apt 3, unit b, ste 2).
    5. Map full suffix words to abbreviations (avenue→ave, etc.).
    """
    if not raw:
        return ""
    street = raw.split(",")[0].strip().lower().rstrip(".")
    street = _UNIT_RE.sub("", street)
    words = street.split()
    if words and words[-1].rstrip(".") in _SUFFIX_MAP:
        words[-1] = _SUFFIX_MAP[words[-1].rstrip(".")]
    elif words and words[-1].rstrip(".") in _SUFFIX_MAP.values():
        words[-1] = words[-1].rstrip(".")
    return " ".join(words)


def _safe_median(values):
    """Return median of a non-empty numeric list, or None."""
    if not values:
        return None
    return median(values)


def _safe_median_int(values):
    """Return median of a non-empty numeric list as int, or None."""
    if not values:
        return None
    return int(round(median(values)))


def _str_money(value):
    """
    Normalize a money value to a whole-dollar string.

    Accepts int, float, Decimal, str, None.
    Returns a string like "2100" or None if the value is missing or
    unparseable.

    Uses banker's rounding (ROUND_HALF_UP) on the final integer.
    """
    if value is None:
        return None
    try:
        d = Decimal(str(value))
        rounded = d.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return str(rounded)
    except (InvalidOperation, ValueError, TypeError):
        logger.warning(
            "market_comps_unparseable_money",
            extra={"value": repr(value), "type": type(value).__name__},
        )
        return None


def _detect_price_drops(comp, period_start, period_end):
    """
    Return list of price drop dicts from comp's price_history within the period.

    Each dict: {"address": str, "from": str, "to": str, "date": str}.
    Only decreases are returned — increases are not "drops."
    """
    history = comp.get("price_history") or []
    drops = []
    for entry in history:
        entry_date_raw = entry.get("date")
        if not entry_date_raw:
            continue
        try:
            entry_date = date.fromisoformat(str(entry_date_raw)[:10])
        except (ValueError, TypeError):
            continue
        if not (period_start <= entry_date <= period_end):
            continue

        old_val = entry.get("old_price") or entry.get("from")
        new_val = entry.get("new_price") or entry.get("to")
        if old_val is None or new_val is None:
            continue

        try:
            old_num = float(old_val)
            new_num = float(new_val)
        except (ValueError, TypeError):
            continue

        if new_num < old_num:
            drops.append({
                "address": comp.get("address", ""),
                "from": _str_money(old_val),
                "to": _str_money(new_val),
                "date": entry_date.isoformat(),
            })
    return drops


def _parse_date(raw):
    """Parse a date string to a date object, or None."""
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Main reducer
# ---------------------------------------------------------------------------

def reduce_comps(
    raw_comps,
    our_address,
    our_rent,
    our_sqft,
    beds,
    radius_miles,
    period_start,
    period_end,
    today=None,
):
    """
    Reduce a list of raw market comps into a snapshot-ready FLAT dict.

    Args:
        raw_comps: list of comp dicts from RentEngine getMarketToolComps.
        our_address: display address of the subject unit.
        our_rent: current asking rent (int, float, Decimal, or str).
        our_sqft: square footage of the subject unit (int or None).
        beds: bedroom count used for the API query.
        radius_miles: radius used for the API query.
        period_start: date, inclusive start of the weekly window.
        period_end: date, inclusive end of the weekly window.
        today: date, defaults to now in ET.

    Returns:
        Flat dict with exactly these keys:
            beds, radius_miles, our_rent, our_sqft,
            comps_returned, comps_in_set, excluded_counts,
            available_count, available_median_rent, available_median_dom,
            off_market_count, off_market_median_rent, off_market_median_dom,
            off_market_window_start, off_market_window_end,
            came_off_market_in_period, new_listings_in_period,
            price_drops_in_period, closest_available, apartment_context.
    """
    if today is None:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("America/New_York")).date()

    our_addr_norm = _normalize_address(our_address)

    # --- Infer our_property_type from raw comps matching our address ---
    # Used internally for closest_available ranking, NOT in output.
    our_property_type = None
    for comp in raw_comps:
        if _normalize_address(comp.get("address")) == our_addr_norm:
            our_property_type = comp.get("property_type")
            break

    # --- Sequential exclusion pipeline ---
    remaining = list(raw_comps)
    excluded_counts = {}

    # Step 1: apartment — property_type == "Apartment"
    before = len(remaining)
    apartments = [c for c in remaining if c.get("property_type") == "Apartment"]
    remaining = [c for c in remaining if c.get("property_type") != "Apartment"]
    excluded_counts["apartment"] = before - len(remaining)

    # Step 2: in_apartment_complex — in_apartment_complex is truthy
    before = len(remaining)
    remaining = [
        c for c in remaining if not c.get("in_apartment_complex")
    ]
    excluded_counts["in_apartment_complex"] = before - len(remaining)

    # Step 3: self — address matches our_address
    before = len(remaining)
    remaining = [
        c for c in remaining
        if _normalize_address(c.get("address")) != our_addr_norm
    ]
    excluded_counts["self"] = before - len(remaining)

    # Step 4: sqft_outlier — if our_sqft is known, exclude comps whose sqft
    # differs by more than 50%
    if our_sqft and our_sqft > 0:
        before = len(remaining)
        low = our_sqft * 0.5
        high = our_sqft * 1.5
        kept = []
        for c in remaining:
            c_sqft = c.get("sqft")
            if c_sqft is None:
                kept.append(c)
            else:
                try:
                    c_sqft_num = float(c_sqft)
                except (ValueError, TypeError):
                    kept.append(c)
                    continue
                if low <= c_sqft_num <= high:
                    kept.append(c)
        remaining = kept
        excluded_counts["sqft_outlier"] = before - len(remaining)
    else:
        excluded_counts["sqft_outlier"] = 0

    # Step 5: rent_null_or_zero — exclude comps with no rent or rent == 0
    before = len(remaining)
    kept = []
    for c in remaining:
        rent = c.get("rent")
        if rent is None:
            continue
        try:
            if float(rent) <= 0:
                continue
        except (ValueError, TypeError):
            continue
        kept.append(c)
    remaining = kept
    excluded_counts["rent_null_or_zero"] = before - len(remaining)

    comps_in_set = len(remaining)

    # --- Split into available vs off-market ---
    # API returns status "available" and "rented".  Anything else is ignored.
    available = [c for c in remaining if c.get("status") == "available"]
    off_market = [c for c in remaining if c.get("status") == "rented"]

    # Available aggregates
    available_rents = []
    available_doms = []
    for c in available:
        try:
            available_rents.append(float(c["rent"]))
        except (ValueError, TypeError, KeyError):
            pass
        dom = c.get("days_on_market")
        if dom is not None:
            try:
                available_doms.append(int(dom))
            except (ValueError, TypeError):
                pass

    # Off-market aggregates — only comps with date_rented within the
    # lookback window contribute to counts, medians, and window bounds.
    # Comps with no date_rented are excluded from aggregates (we cannot
    # place them in time) but still count in comps_in_set.
    lookback_cutoff = today - timedelta(days=OFF_MARKET_LOOKBACK_DAYS)
    off_market_recent = []
    for c in off_market:
        dr = _parse_date(c.get("date_rented"))
        if dr is not None and dr >= lookback_cutoff:
            off_market_recent.append(c)

    off_market_rents = []
    off_market_doms = []
    off_market_dates = []
    for c in off_market_recent:
        try:
            off_market_rents.append(float(c["rent"]))
        except (ValueError, TypeError, KeyError):
            pass
        dom = c.get("days_on_market")
        if dom is not None:
            try:
                off_market_doms.append(int(dom))
            except (ValueError, TypeError):
                pass
        dr = _parse_date(c.get("date_rented"))
        if dr is not None:
            off_market_dates.append(dr)

    off_market_window_start = None
    off_market_window_end = None
    if off_market_dates:
        off_market_window_start = min(off_market_dates).isoformat()
        off_market_window_end = max(off_market_dates).isoformat()

    # --- Period-scoped events ---

    # came_off_market_in_period: comps with date_rented in [period_start, period_end]
    came_off_examples = []
    for c in remaining:
        dr = _parse_date(c.get("date_rented"))
        if dr is not None and period_start <= dr <= period_end:
            came_off_examples.append({
                "address": c.get("address", ""),
                "rent": _str_money(c.get("rent")),
                "date": dr.isoformat(),
            })

    # new_listings_in_period: available comps with days_on_market <= period length
    period_days = (period_end - period_start).days
    new_listing_examples = []
    for c in available:
        dom = c.get("days_on_market")
        if dom is not None:
            try:
                if int(dom) <= period_days:
                    new_listing_examples.append({
                        "address": c.get("address", ""),
                        "rent": _str_money(c.get("rent")),
                        "days_on_market": int(dom),
                    })
            except (ValueError, TypeError):
                pass

    # price_drops_in_period
    price_drops = []
    for c in remaining:
        price_drops.extend(_detect_price_drops(c, period_start, period_end))

    # --- Closest available (ranked by sqft difference from our unit) ---
    closest_available = []
    if our_sqft and our_sqft > 0:
        sortable = []
        for c in available:
            c_sqft = c.get("sqft")
            if c_sqft is not None:
                try:
                    diff = abs(float(c_sqft) - our_sqft)
                    sortable.append((diff, c))
                except (ValueError, TypeError):
                    pass
        sortable.sort(key=lambda x: x[0])
        for _, c in sortable[:3]:
            closest_available.append({
                "address": c.get("address", ""),
                "rent": _str_money(c.get("rent")),
                "sqft": c.get("sqft"),
                "days_on_market": c.get("days_on_market"),
            })
    else:
        # No sqft — fall back to DOM ascending (freshest first)
        sortable = []
        for c in available:
            dom = c.get("days_on_market")
            try:
                sortable.append((int(dom) if dom is not None else 999999, c))
            except (ValueError, TypeError):
                sortable.append((999999, c))
        sortable.sort(key=lambda x: x[0])
        for _, c in sortable[:3]:
            closest_available.append({
                "address": c.get("address", ""),
                "rent": _str_money(c.get("rent")),
                "sqft": c.get("sqft"),
                "days_on_market": c.get("days_on_market"),
            })

    # --- Apartment context (from excluded apartment comps) ---
    apartment_context = None
    if apartments:
        apt_available = [c for c in apartments if c.get("status") == "available"]
        apt_rents = []
        for c in apt_available:
            try:
                apt_rents.append(float(c["rent"]))
            except (ValueError, TypeError, KeyError):
                pass
        apartment_context = {
            "available_count": len(apt_available),
            "median_rent": _str_money(_safe_median(apt_rents)),
        }

    return {
        "beds": beds,
        "radius_miles": radius_miles,
        "our_rent": _str_money(our_rent),
        "our_sqft": our_sqft,
        "comps_returned": len(raw_comps),
        "comps_in_set": comps_in_set,
        "excluded_counts": excluded_counts,
        "available_count": len(available),
        "available_median_rent": _str_money(_safe_median(available_rents)),
        "available_median_dom": _safe_median_int(available_doms),
        "off_market_count": len(off_market_recent),
        "off_market_median_rent": _str_money(_safe_median(off_market_rents)),
        "off_market_median_dom": _safe_median_int(off_market_doms),
        "off_market_window_start": off_market_window_start,
        "off_market_window_end": off_market_window_end,
        "came_off_market_in_period": {
            "count": len(came_off_examples),
            "examples": came_off_examples,
        },
        "new_listings_in_period": {
            "count": len(new_listing_examples),
            "examples": new_listing_examples,
        },
        "price_drops_in_period": {
            "count": len(price_drops),
            "examples": price_drops,
        },
        "closest_available": closest_available,
        "apartment_context": apartment_context,
    }
