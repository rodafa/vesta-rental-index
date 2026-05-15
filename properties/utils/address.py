"""
Address normalization utilities for comparison purposes.

NOT for display — produces a canonical comparison form only.
"""

import re

# Street suffix mappings: long form → canonical abbreviation.
# Applied as whole-word replacements to avoid mangling names like "Broadway".
_SUFFIX_MAP = {
    "road": "rd",
    "street": "st",
    "avenue": "ave",
    "boulevard": "blvd",
    "drive": "dr",
    "lane": "ln",
    "court": "ct",
    "place": "pl",
    "trail": "trl",
    "parkway": "pkwy",
    "terrace": "ter",
    "circle": "cir",
    "highway": "hwy",
}

# Build a single regex alternation for all long-form suffixes.
_SUFFIX_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _SUFFIX_MAP) + r")\b"
)

# Unit indicator tokens that all collapse to "#".
_UNIT_TOKENS = re.compile(
    r"\b(apt|apartment|unit|suite|ste)\b\.?\s*", re.IGNORECASE
)

# Trailing geo fragment: ", City, ST, 28804" or ", City, ST 28804-1234"
_GEO_TAIL = re.compile(
    r",?\s+[a-z ]+,\s*[a-z]{2},?\s*\d{5}(-\d{4})?\s*$"
)

# Hash/pound with optional space before unit id: "# B" → "#b"
_HASH_NORMALIZE = re.compile(r"#\s*")

# Hyphen-separated unit designator: "123 Main St - A" → "123 main st #a"
_HYPHEN_UNIT = re.compile(r"\s+-\s+(\w+)\s*$")

# Duplicate unit designator: "#2 #2" → "#2" (RE often has "#2 Unit 2")
_DUPE_UNIT = re.compile(r"(#\w+)\s+\1\b")


def normalize_address(address: str) -> str:
    """Normalize an address string for comparison purposes.

    Handles common variations:
    - Street suffix abbreviation (Road/Rd, Street/St, Avenue/Ave, etc.)
    - Unit indicator normalization (Apt/Unit/Suite/# → "#")
    - Trailing city/state/zip stripping
    - Hyphen-separated unit designators ("- A" → "#a")
    - Whitespace, punctuation, casing normalization

    Returns a canonical comparison form. NOT for display.
    """
    if not address:
        return ""

    s = address.strip().lower()

    # Strip trailing geo fragment
    s = _GEO_TAIL.sub("", s)

    # Remove periods and commas
    s = s.replace(".", "").replace(",", "")

    # Normalize hyphen-separated unit designator ("123 Main St - A" → "123 main st #a")
    s = _HYPHEN_UNIT.sub(r" #\1", s)

    # Normalize unit indicator tokens to "#"
    s = _UNIT_TOKENS.sub("#", s)

    # Normalize "#  B" → "#b"
    s = _HASH_NORMALIZE.sub("#", s)

    # Normalize street suffixes (whole-word only)
    s = _SUFFIX_PATTERN.sub(lambda m: _SUFFIX_MAP[m.group(1)], s)

    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()

    # Deduplicate repeated unit designators ("#2 #2" → "#2")
    s = _DUPE_UNIT.sub(r"\1", s)

    return s
