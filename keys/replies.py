"""Pure Slack mrkdwn formatters for KeyBot replies. No DB access."""

from keys.services import unit_label


def _escape(text: str) -> str:
    """Escape &, <, > for Slack mrkdwn."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _display_address(address: str, unit_number: str) -> str:
    """Format deduped address for Slack display, then escape.

    Delegates to unit_label for the La Salle-style dedup, using the
    em-dash separator that Slack messages expect.
    """
    return _escape(unit_label(address, "", unit_number, separator=" \u2014 "))


def _format_tag_line(tag: dict) -> str:
    """Single tag line: key ID, status, ring notes, checkout info."""
    parts = [f"*{tag['key_id']}*"]
    if tag["checkout"]:
        parts.append(
            f"(checked out to {_escape(tag['checkout']['name'])}"
            f" since {tag['checkout']['since']})"
        )
    else:
        parts.append(f"({tag['status_raw'].replace('_', ' ')})")
    ring = tag.get("ring_notes")
    if ring:
        parts.append(f"\u2014 ring: {_escape(ring)}")
    else:
        parts.append("\u2014 ring: \u2014")
    return " ".join(parts)


def _format_note_line(note: dict) -> str:
    """Single access note: text (truncated 300), source, date, author."""
    text = note["text"]
    if len(text) > 300:
        text = text[:297] + "\u2026"
    text = _escape(text)
    source = note["source"]
    author = note.get("author")
    date_str = note.get("date")
    if author:
        # KeyBot notes: "KeyBot · Nov 22, 2026 · Alice"
        parts = [source]
        if date_str:
            parts.append(date_str)
        parts.append(_escape(author))
        label = f"_({' \u00b7 '.join(parts)})_"
    elif date_str:
        label = f"_({source}, {date_str})_"
    else:
        label = f"_({source})_"
    return f"\u2022 {text} {label}"


def format_unit(info: dict) -> str:
    """Full reply for a single unit match."""
    lines = [f"*{_display_address(info['address'], info['unit_number'])}*"]

    tags = info.get("tags", [])
    prop_tags = info.get("property_tags", [])

    if not tags and not prop_tags:
        lines.append("\U0001f511 No key on file")
    else:
        for tag in tags:
            lines.append(f"\U0001f511 Key: {_format_tag_line(tag)}")
        if len(tags) >= 2:
            ids = ", ".join(t["key_id"] for t in tags)
            lines.append(
                f"\u26a0\ufe0f {len(tags)} tags on this unit ({ids})"
                f" \u2014 duplicate, needs cleanup"
            )
        for tag in prop_tags:
            lines.append(f"\U0001f511 Building key: {_format_tag_line(tag)}")

    notes = info.get("notes", [])
    if not notes:
        lines.append("\U0001f510 No access notes on file")
    else:
        lines.append("\U0001f510 Access notes:")
        shown = notes[:5]
        for n in shown:
            lines.append(_format_note_line(n))
        remaining = len(notes) - len(shown)
        if remaining > 0:
            lines.append(f"\u2026and {remaining} more")

    return "\n".join(lines)


def format_unit_list(units_with_info: list[dict], total_count: int) -> str:
    """Multi-match reply. units_with_info has at most 10 pre-fetched items."""
    lines = [f"Found {total_count} units at this address:\n"]
    for info in units_with_info:
        label = _display_address(info["address"], info["unit_number"])
        tags = info.get("tags", [])
        if tags:
            tag_str = ", ".join(t["key_id"] for t in tags)
            lines.append(f"\u2022 *{label}* \u2014 \U0001f511 {tag_str}")
        else:
            lines.append(f"\u2022 *{label}* \u2014 no key on file")
    remaining = total_count - len(units_with_info)
    if remaining > 0:
        lines.append(f"\n\u2026and {remaining} more \u2014 add a unit number")
    else:
        lines.append("\nAdd a unit number to see full details.")
    return "\n".join(lines)


def format_tag(info: dict) -> str:
    """Key-ID lookup reply from a tag_info dict."""
    lines = [f"\U0001f511 *{info['key_id']}*"]

    if info["is_orphan"]:
        lines.append("Orphan \u2014 not linked to a unit or property")
    elif info.get("unit_address") is not None:
        label = _display_address(info["unit_address"], info.get("unit_number", ""))
        lines.append(f"Assigned to *{label}*")
    elif info.get("property_address") is not None:
        lines.append(f"Building key for *{_escape(info['property_address'])}*")

    if info["checkout"]:
        lines.append(
            f"Checked out to {_escape(info['checkout']['name'])}"
            f" since {info['checkout']['since']}"
        )
    else:
        lines.append(f"Status: {info['status_raw'].replace('_', ' ')}")

    ring = info.get("ring_notes")
    if ring:
        lines.append(f"Ring: {_escape(ring)}")

    return "\n".join(lines)


def format_not_found(query: str) -> str:
    """No results reply."""
    q = _escape(query[:80])
    return f"No results for \"{q}\". Try a street address or key ID (e.g. A-038)."


def format_help() -> str:
    """Usage instructions."""
    return (
        "*KeyBot* \u2014 key & lockbox lookups + checkouts\n"
        "\n"
        "*Lookups*\n"
        "\u2022 `@KeyBot 28 Bass Lane Unit 1` \u2014 look up a unit\n"
        "\u2022 `@KeyBot 28 Bass` \u2014 list all units at an address\n"
        "\u2022 `@KeyBot A-038` \u2014 look up a key by tag ID\n"
        "\n"
        "*Checkouts*\n"
        "\u2022 `@KeyBot take A-038` \u2014 check out a key\n"
        "\u2022 `@KeyBot return A-038` \u2014 return a key\n"
        "\u2022 `@KeyBot whats out` \u2014 list open checkouts\n"
        "\n"
        "*Updates* (editors only)\n"
        "\u2022 `@KeyBot set code 28 Bass Lane to 1234` \u2014 set lockbox code\n"
        "\u2022 `@KeyBot assign A-038 to 28 Bass Lane` \u2014 assign key to unit\n"
        "\u2022 `@KeyBot retire A-038` \u2014 retire a key\n"
        "\u2022 `@KeyBot missing A-038` \u2014 mark key missing\n"
        "\u2022 `@KeyBot found A-038` \u2014 mark key found\n"
        "\n"
        "\u2022 `@KeyBot health` \u2014 key health report\n"
        "\u2022 `@KeyBot help` \u2014 this message"
    )


def format_did_you_mean(query: str, units_with_info: list[dict],
                        total_count: int) -> str:
    """Suggestion reply when no exact address match found."""
    q = _escape(query[:80])
    lines = [f'No exact match for "{q}". Did you mean:\n']
    for info in units_with_info:
        label = _display_address(info["address"], info["unit_number"])
        tags = info.get("tags", [])
        if tags:
            tag_str = ", ".join(t["key_id"] for t in tags)
            lines.append(f"\u2022 *{label}* \u2014 \U0001f511 {tag_str}")
        else:
            lines.append(f"\u2022 *{label}* \u2014 no key on file")
    remaining = total_count - len(units_with_info)
    if remaining > 0:
        lines.append(f"\n\u2026and {remaining} more")
    return "\n".join(lines)


# ── Checkout / Return / Reminder formatters ─────────────────────────


def _duration_label(checked_out_at, now) -> str:
    """Human-readable duration, e.g. '2 days' or '5 hours'."""
    delta = now - checked_out_at
    days = delta.days
    if days >= 1:
        return f"{days} day{'s' if days != 1 else ''}"
    hours = delta.seconds // 3600
    if hours >= 1:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return "less than an hour"


def format_checkout_ok(key_id: str, display_name: str) -> str:
    """Confirmation after a successful checkout."""
    return f"\u2705 *{_escape(key_id)}* checked out to {_escape(display_name)}."


def format_return_ok(key_id: str, display_name: str) -> str:
    """Confirmation after a successful return."""
    return f"\u2705 *{_escape(key_id)}* returned by {_escape(display_name)}."


def format_whats_out(checkouts, now) -> str:
    """List of open checkouts. *checkouts* is a list of dicts.

    Each dict: key_id, checked_out_by_name, checked_out_by_slack_id,
               checked_out_at.
    """
    if not checkouts:
        return "No keys are currently checked out."
    lines = [f"*{len(checkouts)} key{'s' if len(checkouts) != 1 else ''} checked out:*\n"]
    for co in checkouts:
        dur = _duration_label(co["checked_out_at"], now)
        lines.append(
            f"\u2022 *{co['key_id']}* \u2014"
            f" <@{co['checked_out_by_slack_id']}>"
            f" ({dur})"
        )
    return "\n".join(lines)


def reminder_blocks(
    key_id: str,
    slack_user_id: str,
    checked_out_at,
    now,
    checkout_id: int,
    is_overdue: bool = False,
) -> tuple[str, list]:
    """Build text + Block Kit blocks for a reminder message.

    Returns (text, blocks).  ``text`` is the notification fallback.
    ``is_overdue`` (day 3+) swaps ⏰ for ⚠️.
    """
    dur = _duration_label(checked_out_at, now)
    icon = "\u26a0\ufe0f" if is_overdue else "\u23f0"
    text = (
        f"{icon} <@{slack_user_id}> still has *{_escape(key_id)}*"
        f" ({dur})"
    )
    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "\u2705 Return it"},
                    "action_id": "keybot_reminder_return",
                    "value": f"{checkout_id}",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "\U0001f552 Still have it"},
                    "action_id": "keybot_snooze",
                    "value": f"{checkout_id}",
                },
            ],
        },
    ]
    return text, blocks


def reminder_resolved_text(key_id: str, action: str, display_name: str) -> str:
    """Updated message text after a reminder button is pressed.

    ``action`` is 'returned' or 'snoozed'.
    """
    if action == "returned":
        return f"\u2705 *{_escape(key_id)}* returned by {_escape(display_name)}."
    return (
        f"\U0001f552 *{_escape(key_id)}* \u2014 {_escape(display_name)}"
        f" still has it (snoozed)."
    )


# ── Lookup action buttons ───────────────────────────────────────────


def take_buttons(tags: list[dict]) -> list[dict]:
    """Block Kit buttons for on_hook (Take) and checked_out (Return) tags.

    Returns a list of button elements, max 3.
    *tags* are tag dicts (must have key_id, status_raw).
    action_ids are suffixed with the key_id to ensure uniqueness
    (e.g. ``keybot_take:A-038``).
    """
    buttons = []
    for tag in tags:
        if len(buttons) >= 3:
            break
        kid = tag["key_id"]
        if tag["status_raw"] == "on_hook":
            buttons.append({
                "type": "button",
                "text": {"type": "plain_text", "text": f"Take {kid}"},
                "action_id": f"keybot_take:{kid}",
                "value": kid,
            })
        elif tag["status_raw"] == "checked_out":
            buttons.append({
                "type": "button",
                "text": {"type": "plain_text", "text": f"Return {kid}"},
                "action_id": f"keybot_return:{kid}",
                "value": kid,
            })
    return buttons


def return_button(key_id: str) -> dict:
    """Single Return button for a checked-out key."""
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": f"Return {key_id}"},
        "action_id": f"keybot_return:{key_id}",
        "value": key_id,
    }


def lookup_blocks(text: str, buttons: list[dict]) -> list[dict] | None:
    """Wrap lookup text + action buttons into Block Kit blocks.

    Returns None when there are no buttons (caller sends text-only).
    """
    if not buttons:
        return None
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {"type": "actions", "elements": buttons},
    ]


def confirm_cancel_blocks(preview_text: str, pending_id: int) -> tuple[str, list]:
    """Wrap a pending-change preview with Confirm / Cancel buttons.

    Returns (text, blocks).  The button value carries only the pending id.
    """
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": preview_text}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "\u2705 Confirm"},
                    "action_id": f"keybot_confirm:{pending_id}",
                    "value": str(pending_id),
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "\u274c Cancel"},
                    "action_id": f"keybot_cancel:{pending_id}",
                    "value": str(pending_id),
                    "style": "danger",
                },
            ],
        },
    ]
    return preview_text, blocks


# ── Key Health ─────────────────────────────────────────────────────

def _chunk_urgent(lines: list[str], max_lines: int = 5) -> list[dict]:
    """Split a list of mrkdwn lines into multiple section blocks.

    Each block has at most *max_lines* lines so Slack doesn't fold them
    behind a "Show more" collapse.
    """
    blocks = []
    for i in range(0, len(lines), max_lines):
        chunk = lines[i : i + max_lines]
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(chunk)},
        })
    return blocks


_SECTION_LIMIT = 3000


def _split_section_text(text, limit=_SECTION_LIMIT):
    """Split mrkdwn text at line boundaries to stay under Slack's limit."""
    if len(text) <= limit:
        return [text]
    chunks = []
    buf = []
    buf_len = 0
    for line in text.split("\n"):
        added = len(line) + (1 if buf else 0)
        if buf and buf_len + added > limit:
            chunks.append("\n".join(buf))
            buf = [line]
            buf_len = len(line)
        else:
            buf.append(line)
            buf_len += added
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def format_key_health(health: dict) -> tuple[str, list]:
    """Format key health report as (text, blocks).

    *text* is the full report as a single mrkdwn string — used as the
    ``post_message`` text param and as the ``_safe_reply`` fallback when
    blocks fail.  *blocks* is a list of Block Kit section blocks — one
    short section per category so Slack never collapses urgent items
    behind "Show more".
    """
    sections = []        # text sections (joined for plain-text fallback)
    urgent_line_groups = []  # list of line-lists for _chunk_urgent

    # ── Header ──
    sections.append("*\U0001f511 Key Health*")

    # ── URGENT — one section per category ──
    urgent_text_sections = []

    # (a) Vacant: no key AND no code
    items = health["vacant_no_key_no_code"]
    if items:
        n = len(items)
        lines = [
            f"\U0001f6a8 {n} vacant unit{'s' if n != 1 else ''}"
            f" \u2014 no key AND no code:"
        ]
        for i in items:
            lines.append(f"\u2022 {_escape(i['address'])}")
        urgent_text_sections.append("\n".join(lines))
        urgent_line_groups.append(lines)

    # (b) Vacant: no key
    items = health["vacant_no_key"]
    if items:
        n = len(items)
        lines = [
            f"\U0001f511 {n} vacant unit{'s' if n != 1 else ''}"
            f" \u2014 no key:"
        ]
        for i in items:
            lines.append(f"\u2022 {_escape(i['address'])}")
        urgent_text_sections.append("\n".join(lines))
        urgent_line_groups.append(lines)

    # (c) Vacant: no code
    items = health["vacant_no_code"]
    if items:
        n = len(items)
        lines = [
            f"\U0001f510 {n} vacant unit{'s' if n != 1 else ''}"
            f" \u2014 no code:"
        ]
        for i in items:
            lines.append(f"\u2022 {_escape(i['address'])}")
        urgent_text_sections.append("\n".join(lines))
        urgent_line_groups.append(lines)

    # (d) Overdue checkouts > 24h
    items = health["overdue_checkouts"]
    if items:
        n = len(items)
        lines = [f"\u23f0 {n} key{'s' if n != 1 else ''} out > 24h:"]
        for co in items:
            icon = "\u26a0\ufe0f" if co["is_72h"] else "\u2022"
            lines.append(
                f"{icon} *{co['key_id']}* \u2014 "
                f"{_escape(co['checked_out_by_name'])} "
                f"({co['hours']}h) @ {_escape(co['address'])}"
            )
        urgent_text_sections.append("\n".join(lines))
        urgent_line_groups.append(lines)

    # (e) Missing tags
    items = health["missing_tags"]
    if items:
        n = len(items)
        lines = [
            f"\U0001f3f7\ufe0f {n} tag{'s' if n != 1 else ''}"
            f" marked missing:"
        ]
        for t in items:
            lines.append(
                f"\u2022 *{t['key_id']}* \u2014 {_escape(t['address'])}"
            )
        urgent_text_sections.append("\n".join(lines))
        urgent_line_groups.append(lines)

    # (f) Duplicate tags (unit-level)
    items = health["duplicate_tags"]
    if items:
        n = len(items)
        lines = [
            f"\u26a0\ufe0f {n} unit{'s' if n != 1 else ''}"
            f" with duplicate tags:"
        ]
        for d in items:
            ids = ", ".join(d["key_ids"])
            lines.append(f"\u2022 {_escape(d['address'])} \u2014 {ids}")
        urgent_text_sections.append("\n".join(lines))
        urgent_line_groups.append(lines)

    # (f2) Building tags to sort (property-level)
    items = health.get("building_tags", [])
    if items:
        n = len(items)
        lines = [
            f"\U0001f3e2 {n} propert{'ies' if n != 1 else 'y'}"
            f" with building tags to sort:"
        ]
        for d in items:
            ids = ", ".join(d["key_ids"])
            lines.append(f"\u2022 {_escape(d['address'])} \u2014 {ids}")
        urgent_text_sections.append("\n".join(lines))
        urgent_line_groups.append(lines)

    if urgent_text_sections:
        sections.append("*URGENT*")
        sections.extend(urgent_text_sections)
    else:
        sections.append("\u2705 No urgent key issues.")

    # ── CLEANUP — one section per category ──
    cleanup_sections = []

    # (g) Orphan tags
    items = health["orphan_tags"]
    if items:
        n = len(items)
        lines = [
            f"\U0001f47b {n} orphan tag{'s' if n != 1 else ''}"
            f" (no unit/property):"
        ]
        for t in items[:10]:
            addr = t["imported_address"] or "\u2014"
            lines.append(
                f"\u2022 {t['key_id']} \u2014 \"{_escape(addr)}\""
            )
        if n > 10:
            lines.append(f"+{n - 10} more")
        cleanup_sections.append("\n".join(lines))

    # (h) Occupied: no key
    items = health["occupied_no_key"]
    if items:
        n = len(items)
        lines = [f"\U0001f3e0 {n} occupied \u2014 no key:"]
        for i in items[:10]:
            lines.append(f"\u2022 {_escape(i['address'])}")
        if n > 10:
            lines.append(f"+{n - 10} more")
        cleanup_sections.append("\n".join(lines))

    # (h) Occupied: no code
    items = health["occupied_no_code"]
    if items:
        n = len(items)
        lines = [f"\U0001f3e0 {n} occupied \u2014 no code:"]
        for i in items[:10]:
            lines.append(f"\u2022 {_escape(i['address'])}")
        if n > 10:
            lines.append(f"+{n - 10} more")
        cleanup_sections.append("\n".join(lines))

    if cleanup_sections:
        sections.append("*CLEANUP*")
        sections.extend(cleanup_sections)

    # ── COUNTS ONLY ──
    nk = health["unknown_no_key_count"]
    nc = health["unknown_no_code_count"]
    if nk or nc:
        sections.append(
            "*COUNTS ONLY*\n"
            f"\u2753 Not in RentEngine: {nk} no key, {nc} no code"
        )

    # ── Build output ──
    text = "\n\n".join(sections)

    # Build blocks: header + URGENT header, then chunked urgent categories,
    # then remaining (cleanup, counts) as regular split sections.
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": sections[0]}},
    ]

    if urgent_line_groups:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": "*URGENT*"}},
        )
        for line_group in urgent_line_groups:
            blocks.extend(_chunk_urgent(line_group))
        # remaining sections start after header + "URGENT" + urgent text sections
        remaining_start = 2 + len(urgent_text_sections)
    else:
        # "No urgent key issues" is sections[1]
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": sections[1]}},
        )
        remaining_start = 2

    for section in sections[remaining_start:]:
        for chunk in _split_section_text(section):
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": chunk},
            })

    return text, blocks
