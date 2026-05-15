from django.test import SimpleTestCase

from properties.utils.address import normalize_address


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

    def test_real_world_drift_cases(self):
        """Real drift pairs from the audit should normalize to matching values."""
        pairs = [
            # Reems Creek: suffix + unit indicator
            ("130 Reems Creek Road - 2", "130 Reems Creek Rd #2 Unit 2, Weaverville, NC, 28787"),
            # Roanoke: suffix abbreviation
            ("172 Roanoke Road", "172 Roanoke Rd, Fletcher, NC, 28732"),
            # Greeley: suffix abbreviation
            ("68 Greeley Street", "68 Greeley St, Asheville, NC, 28806"),
            # Mulberry: suffix abbreviation
            ("35 Mulberry Street", "35 Mulberry St, Asheville, NC, 28804"),
            # Penley: suffix + unit indicator
            ("3 Penley Avenue - B", "3 Penley Ave #B Unit B, Asheville, NC, 28804"),
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
