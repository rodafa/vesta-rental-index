"""
Parser for zInspector-originated work-order descriptions.

zInspector pushes one RentVine work order per inspection category.  The
description follows a fixed machine template (raw HTML with <br> tags).
This module extracts structured data from that template.

Pure function — no model imports, no side effects.
"""

import re

# The zInspector stamp, case-insensitive.  Appears after a category prefix.
_ZINSPECTOR_STAMP = re.compile(r"zinspector\s*:", re.IGNORECASE)

# Extract the five-digit-ish reference number after "Reference:".
_REFERENCE = re.compile(r"Reference\s*:\s*(\d+)", re.IGNORECASE)

# Locate the "Work Order Instructions:" header.
_WO_INSTRUCTIONS = re.compile(r"Work Order Instructions\s*:", re.IGNORECASE)

# Locate the "Media Link:" trailer.
_MEDIA_LINK = re.compile(r"Media Link\s*:", re.IGNORECASE)

# Item marker: <br> followed by optional whitespace, a hyphen, then whitespace.
# This is the ONLY reliable split point — bare <br> can appear WITHIN an item.
_ITEM_MARKER = re.compile(r"<br\s*/?>[\s]*-\s+", re.IGNORECASE)

# Strip HTML tags for cleaning individual item text.
_HTML_TAG = re.compile(r"<[^>]+>")

# Collapse whitespace runs.
_WS_RUN = re.compile(r"\s+")


def _parse_item(raw_text: str) -> dict:
    """Parse a single item string into {room, component, detail}.

    Expected format: ``Room : Component | detail``.
    Falls back gracefully when separators are missing.
    """
    room = ""
    component = ""
    detail = raw_text

    # Split on " : " for room
    if " : " in raw_text:
        room_part, rest = raw_text.split(" : ", 1)
        room = room_part.strip()
        # Split on " | " for component/detail
        if " | " in rest:
            component_part, detail_part = rest.split(" | ", 1)
            component = component_part.strip()
            detail = detail_part.strip()
        else:
            detail = rest.strip()

    return {"room": room, "component": component, "detail": detail}


def parse_zinspector_description(raw_description: str) -> dict | None:
    """
    Parse a zInspector-originated work-order description.

    Returns None if the description does not contain the zInspector stamp.
    Otherwise returns::

        {
            "category": str,         # e.g. "Maintenance", "Landscaping"
            "human_summary": str,    # free text after "Category:" before stamp (may be "")
            "reference": str|None,   # e.g. "10283"
            "items": [               # structured inspection items
                {"room": str, "component": str, "detail": str},
                ...
            ],
        }

    Never raises — malformed input yields empty/None sub-fields.
    """
    if not raw_description:
        return None

    stamp_match = _ZINSPECTOR_STAMP.search(raw_description)
    if not stamp_match:
        return None

    # --- Category + human summary: text before the stamp ---
    prefix = raw_description[:stamp_match.start()]
    # Strip HTML tags and collapse whitespace
    prefix_clean = _WS_RUN.sub(" ", _HTML_TAG.sub(" ", prefix)).strip()
    # If the category has a colon (e.g. "Landscaping: Garbage to thrown out..."),
    # take only the part before the first colon as category; the rest is the
    # human summary.
    human_summary = ""
    if ":" in prefix_clean:
        category, human_summary = prefix_clean.split(":", 1)
        category = category.strip()
        human_summary = human_summary.strip()
    else:
        category = prefix_clean

    # --- Reference ---
    ref_match = _REFERENCE.search(raw_description)
    reference = ref_match.group(1) if ref_match else None

    # --- Items ---
    items = []
    instr_match = _WO_INSTRUCTIONS.search(raw_description)
    if instr_match:
        after_instructions = raw_description[instr_match.end():]
        # Cut at "Media Link:" if present
        media_match = _MEDIA_LINK.search(after_instructions)
        if media_match:
            items_block = after_instructions[:media_match.start()]
        else:
            items_block = after_instructions

        # Split on item markers
        raw_items = _ITEM_MARKER.split(items_block)
        for raw_item in raw_items:
            # Replace internal <br> with space, strip tags, collapse whitespace
            cleaned = _HTML_TAG.sub(" ", raw_item.replace("<br>", " ").replace("<BR>", " "))
            cleaned = _WS_RUN.sub(" ", cleaned).strip()
            if cleaned:
                items.append(_parse_item(cleaned))

    return {
        "category": category,
        "human_summary": human_summary,
        "reference": reference,
        "items": items,
    }
