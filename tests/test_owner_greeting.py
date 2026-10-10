"""
Tests for _owner_greeting_name() — the helper that picks a safe
first name for "Hi {name}," greetings in owner emails.

No database needed: uses plain SimpleNamespace objects.

Resolution order:
  1. owner.first_name — if present and not digit-starting
  2. owner.contact_first_name — human contact linked to entity's portfolio
  3. first word of owner.name — if not digit-starting and no entity suffix
  4. "there" — safe fallback
"""

from types import SimpleNamespace

from comms.services import _owner_greeting_name


class TestOwnerGreetingName:
    def test_normal_first_name(self):
        """A plain first name is returned as-is."""
        owner = SimpleNamespace(first_name="Amy", name="Amy Marion", contact_first_name="")
        assert _owner_greeting_name(owner) == "Amy"

    def test_numeric_address_entity(self):
        """'90 Moody Ave LLC' — starts with digit, no contact → 'there'."""
        owner = SimpleNamespace(first_name="", name="90 Moody Ave LLC", contact_first_name="")
        assert _owner_greeting_name(owner) == "there"

    def test_entity_with_digit_prefix(self):
        """'Brahma 7 LLC & Ross Hamilton' — entity suffix detected → 'there'."""
        owner = SimpleNamespace(first_name="", name="Brahma 7 LLC & Ross Hamilton", contact_first_name="")
        assert _owner_greeting_name(owner) == "there"

    def test_entity_with_comma(self):
        """'Blue Ridge Acres Assets, LLC' — entity suffix detected → 'there'."""
        owner = SimpleNamespace(first_name="", name="Blue Ridge Acres Assets, LLC", contact_first_name="")
        assert _owner_greeting_name(owner) == "there"

    def test_fallback_to_full_name_first_word(self):
        """'Amy K Marion / Colin Hunt' — no entity suffix, first word used."""
        owner = SimpleNamespace(first_name="", name="Amy K Marion / Colin Hunt", contact_first_name="")
        assert _owner_greeting_name(owner) == "Amy"

    def test_empty_everything(self):
        """All of first_name, contact_first_name, and name empty → 'there'."""
        owner = SimpleNamespace(first_name="", name="", contact_first_name="")
        assert _owner_greeting_name(owner) == "there"

    def test_numeric_first_name_with_entity_full(self):
        """first_name='90' (starts with digit) falls through, entity name → 'there'."""
        owner = SimpleNamespace(first_name="90", name="90 Moody Ave LLC", contact_first_name="")
        assert _owner_greeting_name(owner) == "there"

    # --- contact_first_name fallback (step 2) ---

    def test_entity_with_contact_first_name(self):
        """Entity owner with contact_first_name uses it (the 90 Moody fix)."""
        owner = SimpleNamespace(first_name="", name="90 Moody Ave LLC", contact_first_name="Zacory")
        assert _owner_greeting_name(owner) == "Zacory"

    def test_digit_first_name_falls_to_contact(self):
        """first_name='90' is digit-starting, but contact_first_name is set."""
        owner = SimpleNamespace(first_name="90", name="90 Moody Ave LLC", contact_first_name="Zacory")
        assert _owner_greeting_name(owner) == "Zacory"

    def test_real_first_name_wins_over_contact(self):
        """A real first_name takes priority even if contact_first_name is also set."""
        owner = SimpleNamespace(first_name="Amy", name="Amy Marion", contact_first_name="Zacory")
        assert _owner_greeting_name(owner) == "Amy"

    # --- name-parse fallback (step 3) ---

    def test_non_entity_name_parse_fallback(self):
        """Non-entity human name with no first_name and no contact → first word."""
        owner = SimpleNamespace(first_name="", name="Sara Ream", contact_first_name="")
        assert _owner_greeting_name(owner) == "Sara"
