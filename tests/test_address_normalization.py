from django.test import SimpleTestCase

from properties.utils.address import base_street, normalize_address


class NormalizeAddressTests(SimpleTestCase):
    """Tests for the normalize_address utility."""

    def test_strips_trailing_geo(self):
        """Trailing city/state/zip fragment is stripped."""
        result = normalize_address("123 Main St, Asheville, NC, 28804")
        self.assertEqual(result, "123 main st")

    def test_collapses_street_suffix(self):
        """Long-form street suffixes normalize to abbreviations."""
        self.assertEqual(
            normalize_address("172 Roanoke Road"),
            normalize_address("172 Roanoke Rd"),
        )

    def test_unit_indicator_variations(self):
        """Apt, Unit, and # all normalize to the same form."""
        apt = normalize_address("123 Main St Apt B")
        unit = normalize_address("123 Main St Unit B")
        hash_ = normalize_address("123 Main St #B")
        self.assertEqual(apt, unit)
        self.assertEqual(unit, hash_)

    def test_hyphen_apt_normalizes(self):
        """'- Apt B' hyphen+multi-token normalizes to '#b' (Penley case)."""
        self.assertEqual(
            normalize_address("3 Penley Avenue - Apt B"),
            normalize_address("3 Penley Ave #B Unit B, Asheville, NC, 28804"),
        )

    def test_hyphen_unit_normalizes(self):
        """'- Unit 8' hyphen+multi-token normalizes to '#8' (Reid case)."""
        self.assertEqual(
            normalize_address("26 Reid St - Unit 8"),
            normalize_address("26 Reid St #8 Unit 8, Marion, NC, 28752"),
        )

    def test_real_world_drift_cases(self):
        """Real drift pairs from the audit should normalize to matching values.

        Uses the same form _addresses_match builds: address_line_1 + "Unit " +
        address_line_2 for the local side (matching prod data shape).
        """
        pairs = [
            # Reems Creek: suffix + unit indicator (local: addr1 + Unit + addr2)
            ("130 Reems Creek Road Unit 2", "130 Reems Creek Rd #2 Unit 2, Weaverville, NC, 28787"),
            # Roanoke: suffix abbreviation only
            ("172 Roanoke Road", "172 Roanoke Rd, Fletcher, NC, 28732"),
            # Greeley: suffix abbreviation only
            ("68 Greeley Street", "68 Greeley St, Asheville, NC, 28806"),
            # Mulberry: suffix abbreviation only
            ("35 Mulberry Street", "35 Mulberry St, Asheville, NC, 28804"),
            # Penley: suffix + unit indicator (local: addr1 + Unit + addr2)
            ("3 Penley Avenue Unit B", "3 Penley Ave #B Unit B, Asheville, NC, 28804"),
        ]
        for local, re_addr in pairs:
            self.assertEqual(
                normalize_address(local),
                normalize_address(re_addr),
                f"Mismatch: {local!r} vs {re_addr!r}",
            )

    def test_preserves_distinctness(self):
        """Different addresses stay different after normalization."""
        self.assertNotEqual(
            normalize_address("123 Main St"),
            normalize_address("456 Main St"),
        )

    def test_broadway_not_broken(self):
        """'Broadway' is not mangled by street suffix regex."""
        result = normalize_address("123 Broadway")
        self.assertEqual(result, "123 broadway")


class BaseStreetTests(SimpleTestCase):
    """Tests for the base_street utility."""

    def test_strips_unit_designator(self):
        """Unit designator after '#' is stripped."""
        self.assertEqual(base_street("68 greeley st #a"), "68 greeley st")

    def test_strips_multi_token_after_unit(self):
        """Extra descriptive tokens after unit are stripped."""
        self.assertEqual(
            base_street("68 greeley st #a upstairs #"),
            "68 greeley st",
        )

    def test_strips_trailing_bare_token(self):
        """Bare token after street suffix is stripped."""
        self.assertEqual(base_street("588 ray hill rd a"), "588 ray hill rd")

    def test_no_unit_unchanged(self):
        """Address without unit designator passes through."""
        self.assertEqual(base_street("130 reems creek rd"), "130 reems creek rd")
