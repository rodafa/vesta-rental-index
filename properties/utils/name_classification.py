"""
Name classification utilities.

Determines whether a name belongs to a real person vs. a business entity.
Shared across the project — any workflow that needs to filter out
LLC/Corp/Trust owners can import is_person_name() from here.
"""

import re

# Entity indicator tokens (case-insensitive substring match).
# If any of these appear in the name, it's classified as an entity.
_ENTITY_INDICATORS = [
    "LLC",
    "L.L.C.",
    "Corp",
    "Corporation",
    "Inc",
    "Incorporated",
    "Ltd",
    "Limited",
    "Trust",
    "Properties",
    "Holdings",
    "Investments",
    "Group",
    "Partners",
    "Partnership",
    "Associates",
    "Capital",
    "Ventures",
    "Enterprises",
    "Realty",
    "Rentals",
    "Management",
]

# Build a single compiled regex.
# Uses (?<!\w) and (?!\w) instead of \b so that indicators ending in dots
# (like "L.L.C.") match correctly at end-of-string or before spaces.
_ENTITY_PATTERN = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(ind) for ind in _ENTITY_INDICATORS) + r")(?!\w)",
    re.IGNORECASE,
)


def is_person_name(name: str) -> bool:
    """Return True if the name appears to belong to a real person.

    Returns False if the name contains any business-entity indicator
    (LLC, Corp, Trust, etc.) — case-insensitive.

    An empty/blank name returns False (no valid person name).
    """
    if not name or not name.strip():
        return False
    return _ENTITY_PATTERN.search(name) is None
