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

# Hyphen-separated unit designator: handles single token ("- A") and
# multi-token ("- Apt B", "- Unit 8") by stripping the hyphen separator
# and letting downstream unit-token normalization handle the rest.
_HYPHEN_UNIT = re.compile(r"\s+-\s+")

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

    # Replace " - " separators with " " so unit tokens can be normalized
    # downstream. "123 Main St - Apt B" → "123 main st apt b" → "123 main st #b"
    s = _HYPHEN_UNIT.sub(" ", s)

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


def base_street(normalized: str) -> str:
    """Extract the base street address, stripping any unit designator.

    Takes the output of normalize_address() and strips everything from
    the first '#' onward, plus any trailing descriptive tokens that
    follow a recognized street suffix.

    Examples:
        "68 greeley st #a upstairs #" → "68 greeley st"
        "3 penley ave #b"             → "3 penley ave"
        "588 ray hill rd a"           → "588 ray hill rd"
        "130 reems creek rd"          → "130 reems creek rd"
    """
    if not normalized:
        return ""

    # Strip from first "#" onward
    idx = normalized.find("#")
    if idx > 0:
        return normalized[:idx].strip()

    # Strip trailing tokens after a known street suffix abbreviation
    all_suffixes = set(_SUFFIX_MAP.values())
    words = normalized.split()
    for i in range(len(words) - 1, 0, -1):
        if words[i] in all_suffixes:
            return " ".join(words[: i + 1])

    return normalized
