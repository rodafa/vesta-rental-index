"""
Sanitization helpers for owner-facing maintenance emails.

All shared helpers live here: banned-word substitution, tenant-name scrubbing,
seasonal template detection, date formatting, and output validators.

No model imports at module level — keep this importable without Django apps loaded.
"""

import logging
import re
from datetime import date

import zoneinfo

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1a. Banned words
# ---------------------------------------------------------------------------

# Only mold/mould and their adjective forms.  Word boundaries alone reject
# molding, molded, moldings, moldboard, crown molding, remolding — no
# lookahead needed.  mou?ld matches mold/mould, y? adds moldy/mouldy.
BANNED_WORDS = {"moldy", "mouldy", "mold", "mould"}

_BANNED_RE = re.compile(r"\bmou?ldy?\b", re.IGNORECASE)


def sanitize_banned_words(text: str) -> str:
    """Replace banned words with 'organic growth'. Returns cleaned text."""
    if not text:
        return text
    return _BANNED_RE.sub("organic growth", text)


def validate_no_banned_words(html: str) -> None:
    """Hard-fail if any banned word survives into the final HTML."""
    if not html:
        return
    match = _BANNED_RE.search(html)
    if match:
        raise ValueError(
            f"Banned word found in output HTML: '{match.group()}'"
        )


# ---------------------------------------------------------------------------
# 1b. Tenant-name scrubbing
# ---------------------------------------------------------------------------

# Keyword regex: case-insensitive keywords that precede a tenant name.
# Separate from the name capture so IGNORECASE doesn't affect name matching.
_TENANT_KEYWORD_RE = re.compile(
    r"(?:tenant|resident|reported by|submitted by)\s+",
    re.IGNORECASE,
)

# Case-SENSITIVE name capture: capitalized first + at least one more word.
_PROPER_NAME_RE = re.compile(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)")


def extract_description_names(descriptions: list[str]) -> set[str]:
    """Extract probable tenant names from work-order descriptions.

    Looks for patterns like "tenant John Smith" or "reported by Jane Doe".
    Returns a set of full names found.
    """
    names = set()
    for desc in descriptions:
        if not desc:
            continue
        for kw_match in _TENANT_KEYWORD_RE.finditer(desc):
            # Look for a proper name immediately after the keyword
            rest = desc[kw_match.end():]
            name_match = _PROPER_NAME_RE.match(rest)
            if name_match:
                names.add(name_match.group(1))
    return names


def build_tenant_name_set(portfolio, descriptions: list[str]) -> set[str]:
    """Union of lease tenant names + names extracted from descriptions.

    Imports Lease model inside function to avoid module-level Django dependency.
    """
    from core.models import Lease

    # Active lease tenants for this portfolio (primary_lease_status=2 is
    # "Active" per the PRIMARY_LEASE_STATUS_CHOICES on the model).
    lease_names = set()
    leases = (
        Lease.objects.filter(
            property__portfolio=portfolio,
            primary_lease_status=2,  # Active
        )
        .prefetch_related("tenants")
    )
    for lease in leases:
        for tenant in lease.tenants.all():
            if tenant.first_name and tenant.last_name:
                lease_names.add(f"{tenant.first_name} {tenant.last_name}")
            elif tenant.name:
                lease_names.add(tenant.name)

    extracted = extract_description_names(descriptions)
    all_names = lease_names | extracted
    logger.info(
        "tenant_name_set_built",
        extra={
            "portfolio": str(portfolio),
            "lease_names": len(lease_names),
            "extracted_names": len(extracted),
            "total": len(all_names),
        },
    )
    return all_names


def _build_name_regex(names: set[str]) -> re.Pattern | None:
    """Build a compiled regex that matches any of the given names.

    Returns None if the name set is empty.
    Names are matched as whole words, case-sensitive.
    """
    if not names:
        return None
    # Sort longest-first to ensure "John Smith Jr" matches before "John Smith"
    sorted_names = sorted(names, key=len, reverse=True)
    pattern = r"\b(" + "|".join(re.escape(n) for n in sorted_names) + r")\b"
    return re.compile(pattern)


def scrub_tenant_names(text: str, name_re: re.Pattern | None) -> str:
    """Replace tenant names with '[tenant]'. Returns cleaned text."""
    if not text or name_re is None:
        return text
    return name_re.sub("[tenant]", text)


def validate_no_tenant_names(html: str, name_re: re.Pattern | None) -> None:
    """Hard-fail if any tenant name survives into the final HTML."""
    if not html or name_re is None:
        return
    match = name_re.search(html)
    if match:
        raise ValueError(
            f"Tenant name found in output HTML: '{match.group()}'"
        )


# ---------------------------------------------------------------------------
# 1c. Seasonal inspection
# ---------------------------------------------------------------------------

# Matches "Winter (Nov, Dec, Jan)", "Fall (August, Sept, Oct)", etc.
_SEASON_HEADER_RE = re.compile(
    r"(Winter|Spring|Summer|Fall)\s*\([A-Za-z, ]+\)",
    re.IGNORECASE,
)

# Item marker within a season block: "1.", "2.", "3." etc.
_ITEM_MARKER_RE = re.compile(r"^\d+\.\s*", re.MULTILINE)

ARCHITECT_PLAN_LINE = (
    "This visit is part of your Architect plan, so there is no cost to you."
)


def pick_season(scheduled_date: date | None, send_date: date) -> str:
    """Map a date to a season label.

    Uses scheduled_date when present, send_date as fallback.
    Aug-Oct -> Fall, Nov-Jan -> Winter, Feb-Apr -> Spring, May-Jul -> Summer.
    """
    d = scheduled_date if scheduled_date else send_date
    month = d.month
    if month in (8, 9, 10):
        return "Fall"
    elif month in (11, 12, 1):
        return "Winter"
    elif month in (2, 3, 4):
        return "Spring"
    else:
        return "Summer"


def _parse_season_blocks(raw_desc: str) -> dict[str, str]:
    """Parse raw description into {season_name: block_text} dict.

    Each block runs from its header line to the start of the next header
    (or end of string).  Returns lowercased season keys for lookup.
    """
    matches = list(_SEASON_HEADER_RE.finditer(raw_desc))
    if not matches:
        return {}

    blocks = {}
    for i, m in enumerate(matches):
        season_name = m.group(1).capitalize()
        block_start = m.end()
        block_end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_desc)
        blocks[season_name] = raw_desc[block_start:block_end]
    return blocks


def _extract_block_items(block_text: str) -> list[str]:
    """Extract item texts from a season block, stripping the "N." markers.

    Handles duplicate numbering (e.g. Fall has two items numbered 2).
    Strips trailing "[Source] ..." lines and other non-item content.
    """
    # Strip trailing metadata lines (e.g. "[Source] Requested by: Mason")
    cleaned_block = re.sub(r"\[Source\].*", "", block_text, flags=re.DOTALL)

    # Split on item markers, ignore the empty string before the first marker
    parts = _ITEM_MARKER_RE.split(cleaned_block)
    items = []
    for part in parts:
        cleaned = part.strip().rstrip(".")
        if cleaned:
            items.append(cleaned)
    return items


def build_seasonal_wo_summary(
    raw_desc: str,
    scheduled_date: date | None,
    send_date: date,
) -> dict | None:
    """Detect a seasonal inspection WO and return summary with extracted items.

    Parses the raw description into season blocks, picks the current season
    based on scheduled_date (fallback: send_date), extracts only that season's
    items, and returns a summary_sentence listing them.

    Returns dict with keys {is_seasonal, season, architect_plan_line,
    summary_sentence, items} or None if not a seasonal template.
    """
    if not raw_desc:
        return None

    blocks = _parse_season_blocks(raw_desc)
    if not blocks:
        return None

    season = pick_season(scheduled_date, send_date)
    block_text = blocks.get(season, "")
    items = _extract_block_items(block_text) if block_text else []

    summary_sentence = ", ".join(items) if items else f"{season} seasonal inspection"

    result = {
        "is_seasonal": True,
        "season": season,
        "architect_plan_line": ARCHITECT_PLAN_LINE,
        "summary_sentence": summary_sentence,
        "items": items,
    }
    logger.info(
        "seasonal_inspection_detected",
        extra={
            "season": season,
            "send_date": str(send_date),
            "scheduled_date": str(scheduled_date) if scheduled_date else None,
            "item_count": len(items),
            "items": items,
        },
    )
    return result


# ---------------------------------------------------------------------------
# 1d. Date formatting
# ---------------------------------------------------------------------------

_ET = zoneinfo.ZoneInfo("America/New_York")

_ORDINAL_SUFFIXES = {1: "st", 2: "nd", 3: "rd", 21: "st", 22: "nd", 23: "rd", 31: "st"}


def _ordinal(day: int) -> str:
    """Return day with ordinal suffix: 1st, 2nd, 3rd, 4th, ..."""
    return f"{day}{_ORDINAL_SUFFIXES.get(day, 'th')}"


def format_owner_date(d: date | None) -> str:
    """Format a date for owner-facing display: 'October 19th'.

    Returns empty string for None.
    """
    if d is None:
        return ""
    return f"{d.strftime('%B')} {_ordinal(d.day)}"


def default_send_date() -> date:
    """Today in America/New_York."""
    from datetime import datetime

    return datetime.now(tz=_ET).date()


# ---------------------------------------------------------------------------
# 1e. Third-person owner validator
# ---------------------------------------------------------------------------

_THIRD_PERSON_OWNER_PATTERNS = [
    re.compile(r"\bthe owner\b", re.IGNORECASE),
    re.compile(r"\bowner has been\b", re.IGNORECASE),
    re.compile(r"\binformed the owner\b", re.IGNORECASE),
    re.compile(r"\bno vendor has been assigned\b", re.IGNORECASE),
]


def validate_no_third_person_owner(html: str) -> None:
    """Hard-fail on internal third-person phrasing in owner-facing HTML."""
    if not html:
        return
    for pattern in _THIRD_PERSON_OWNER_PATTERNS:
        match = pattern.search(html)
        if match:
            raise ValueError(
                f"Third-person owner phrasing found in output HTML: '{match.group()}'"
            )
