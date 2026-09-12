"""
Tests for zInspector description parsing and selector integration.

Production-verified fixtures from probe output (scripts/probe_zinspector_fixtures.py):
  - WO 100997: 5 items, Maintenance, reference 10283
  - WO 101081: 1 item (wrapping), Landscaping, reference 10295
  - WO 100988: 32 items, Repairs, reference 10281
  - WO 101120: non-zInspector (negative case)
"""

import pytest

from maintenance.zinspector import parse_zinspector_description


# ---------------------------------------------------------------------------
# Production-verified raw descriptions (verbatim from probe output)
# ---------------------------------------------------------------------------

WO_100997_RAW = 'Maintenance<br><br>zInspector:<br>Reference: 10283<br>Link:<br>https://dashboard.zinspector.com/task/view/2084814<br><br>Work Order Instructions:<br>  -  Bedroom 1: Left : Switch/Outlet | Needs to be removed <br>  -  Hallway/Stairs 1 : Closet/Cabinet | Shelving needs to be put up <br>  -  Living Room 1 : Switch/Outlet | Old outlet to be covered or eliminated <br>  -  Laundry Room 1 : Other | Dryer opening <br>  -  Front Yard/Exterior 1 : Building Exterior | Metal debris to be picked up <br><br>Media Link:<br>https://portfolio.zinspector.com/taskManager/taskGalleryExternal/MjNlAe54bq17MNNviOxRwXpk3aEWoxZY'

WO_101081_RAW = 'Landscaping: Garbage to thrown out and removed grass to be cut<br>Garbage in back yard as well. Trim all bushes cutdown the early bamboo sprouts<br><br>zInspector:<br>Reference: 10295<br>Link:<br>https://dashboard.zinspector.com/task/view/2099999<br><br>Work Order Instructions:<br>  -  Front Yard/Exterior 1 : Landscaping | Garbage to thrown out and removed grass to be cut<br>Garbage in back yard as well. Trim all bushes cutdown the early bamboo sprouts <br><br>Media Link:<br>https://portfolio.zinspector.com/taskManager/taskGalleryExternal/MdyYW2jB9wVr5889uG4w5Xlq45zkRO1P'

WO_100988_RAW = 'Repairs<br><br>zInspector:<br>Reference: 10281<br>Link:<br>https://dashboard.zinspector.com/task/view/2081605<br><br>Work Order Instructions:<br>  -  Bathroom 1: Master : Cabinet/Counter/Shelving | Right side track on top drawer is broken, comes out of place when opened and is difficult to close <br>  -  Bathroom 1: Master : Door/Knob/Lock | Door does not latch <br>  -  Bathroom 1: Master : Toilet | Tank is loose <br>  -  Bathroom 1: Master : Plumbing/Drain | Sink drains slowly and stopper lever does not work <br>  -  Bathroom 1: Master : Tub/Shower | Floor is cracked, and a piece came loose  <br>  -  Bathroom 1: Master : Window/Lock/Screen | Broken frame, broken weatherstripping, window will not stay up unless opened 100% <br>  -  Bathroom 1: Master : Sink/Faucet | See comments under plumbing/drain <br>  -  Bathroom 2: Shared : Toilet | Tank is loose <br>  -  Bathroom 2: Shared : Sink/Faucet | Drains very slowly <br>  -  Bathroom 2: Shared : Window/Lock/Screen | Broken weather stripping  <br>  -  Bedroom 1: Master  : Flooring/Baseboard | Broken floor board at bathroom entryway <br>  -  Bedroom 1: Master  : Window/Lock/Screen | Window to left when entering room (facing external AC blower) will not stay up unless 100% opened, broken weatherstripping; left side window facing side yard will not full open; right side window will not stay open unless fully open, also a little loose w <br>  -  Laundry Room 1: Area covered in bedroom : Washer | Loose metal pieces inside drum, the picture is some that I pulled out but there are more <br>  -  Kitchen 1 : Window/Lock/Screen | Top pane fell when opening window <br>  -  Kitchen 1 : Oven/Microwave | Overhead Light burnt out, missing cover  <br>  -  Kitchen 1 : Range/Fan/Hood/Filter | Rusty grease catcher <br>  -  Kitchen 1 : Microwave | Missing overhead light ; display very dim <br>  -  Dining Room 1: Area covered in kitchen area : Smoke/CO Detector | Smoke detector went off this morning for seemingly no reason <br>  -  Dining Room 1: Area covered in kitchen area : Window/Lock/Screen | See comments under kitchen <br>  -  Living Room 1: Including hall : Flooring/Baseboard | Loose floorboards in hall; also a loose floorboard under the window  <br>  -  Living Room 1: Including hall : Door/Knob/Lock | We are requesting a deadbolt lock to be added to front door for security; also door frame is rotted/damaged; screen door attachment damaged  <br>  -  Bedroom 2: Left : Door/Knob/Lock | Missing strike plate  <br>  -  Bedroom 2: Left : Flooring/Baseboard | Loose/eneven floorboards <br>  -  Bedroom 2: Left : Other | Exposed/loose wiring hidden behind little side table  <br>  -  Bedroom 2: Left : Window/Lock/Screen | Window will not stay open at all <br>  -  Front Yard/Exterior 1 : Light Fixture | Light over driveway does not work, not sure what switch controls it <br>  -  Garage/Parking 1 : Other | Entry door to house: screen door easily came off track when trying to close it; I\u2019m not sure what the function of the button in the photo is; also would like to request a lock be added to the garage entry door if possible <br>  -  Garage/Parking 1 : Other Door/Knob/Lock | See screen door comments under \u201cother\u201d <br>  -  Hallway/Stairs 1: Area covered in LR area : Flooring/Baseboard | See comments in living room <br>  -  Keys/Remotes/Devices 1 : Remotes/Devices | Waiting on garage remote <br>  -  Storage 1 : Other | Doorway unfinished upon move in <br>  -  Systems 1: Area covered throughout rest of inspection : Stove | Rusty Grease catcher  <br><br>Media Link:<br>https://portfolio.zinspector.com/taskManager/taskGalleryExternal/Mk3pvoQ4ae6XWGGlCAb88XDqO9NxnWlK'

WO_101120_RAW = 'Deep clean needed\n\n[Source] Requested by: Property Manager'

# Derived mutation: WO_100997 with everything from "Work Order Instructions:" onward removed.
# Not production data — tests the 0-item fallthrough path.
WO_100997_TRUNCATED_NO_INSTRUCTIONS = WO_100997_RAW[:WO_100997_RAW.index("Work Order Instructions:")]


# ---------------------------------------------------------------------------
# Parser unit tests (no DB)
# ---------------------------------------------------------------------------


class TestParseZinspectorDescription:
    def test_100997_category_reference_count(self):
        result = parse_zinspector_description(WO_100997_RAW)
        assert result is not None
        assert result["category"] == "Maintenance"
        assert result["reference"] == "10283"
        assert len(result["items"]) == 5

    def test_100997_first_and_last_item(self):
        result = parse_zinspector_description(WO_100997_RAW)
        assert "Bedroom 1" in result["items"][0]
        assert "Metal debris to be picked up" in result["items"][-1]

    def test_100997_no_media_link_in_items(self):
        result = parse_zinspector_description(WO_100997_RAW)
        for item in result["items"]:
            assert "Media Link" not in item
            assert "portfolio.zinspector.com" not in item

    def test_101081_single_item_not_split(self):
        result = parse_zinspector_description(WO_101081_RAW)
        assert result is not None
        assert len(result["items"]) == 1
        assert "Garbage to thrown out" in result["items"][0]
        assert "bamboo sprouts" in result["items"][0]

    def test_101081_category_strips_colon_suffix(self):
        result = parse_zinspector_description(WO_101081_RAW)
        assert result["category"] == "Landscaping"

    def test_101081_reference(self):
        result = parse_zinspector_description(WO_101081_RAW)
        assert result["reference"] == "10295"

    def test_100988_count_category_reference(self):
        result = parse_zinspector_description(WO_100988_RAW)
        assert result is not None
        assert len(result["items"]) == 32
        assert result["category"] == "Repairs"
        assert result["reference"] == "10281"

    def test_100988_curly_apostrophe_survives(self):
        result = parse_zinspector_description(WO_100988_RAW)
        assert any(
            "I\u2019m not sure what the function of the button" in item
            for item in result["items"]
        )

    def test_100988_curly_quotes_survive(self):
        result = parse_zinspector_description(WO_100988_RAW)
        assert any(
            "\u201cother\u201d" in item
            for item in result["items"]
        )

    def test_100988_longest_item_not_truncated(self):
        result = parse_zinspector_description(WO_100988_RAW)
        assert any(
            "also a little loose w" in item
            for item in result["items"]
        )

    def test_no_html_tags_in_any_items(self):
        for raw in (WO_100997_RAW, WO_101081_RAW, WO_100988_RAW):
            result = parse_zinspector_description(raw)
            for item in result["items"]:
                assert "<" not in item, f"HTML tag found in item: {item!r}"
                assert ">" not in item, f"HTML tag found in item: {item!r}"

    def test_non_zinspector_returns_none(self):
        assert parse_zinspector_description(WO_101120_RAW) is None

    def test_empty_string_returns_none(self):
        assert parse_zinspector_description("") is None

    def test_none_returns_none(self):
        assert parse_zinspector_description(None) is None

    def test_malformed_no_instructions(self):
        result = parse_zinspector_description(WO_100997_TRUNCATED_NO_INSTRUCTIONS)
        assert result is not None
        assert result["category"] == "Maintenance"
        assert result["reference"] == "10283"
        assert result["items"] == []


# ---------------------------------------------------------------------------
# Selector integration tests (DB required)
# ---------------------------------------------------------------------------


pytestmark = pytest.mark.django_db


@pytest.fixture
def portfolio():
    from core.models import Portfolio
    return Portfolio.objects.create(rentvine_id=500, name="ZI Test Portfolio")


@pytest.fixture
def prop(portfolio):
    from core.models import Property
    return Property.objects.create(
        rentvine_id=500,
        portfolio=portfolio,
        address_line_1="789 Elm St",
        city="Asheville",
        state="NC",
        postal_code="28801",
        is_active=True,
    )


@pytest.fixture
def unit(prop):
    from core.models import Unit
    return Unit.objects.create(
        rentvine_id=500,
        property=prop,
        name="789 Elm St",
        address_line_1="789 Elm St",
        is_active=True,
    )


class TestWorkOrderToDictZinspector:
    def test_100997_punch_list(self, portfolio, prop, unit):
        from maintenance.models import WorkOrder
        from maintenance.selectors import _work_order_to_dict

        wo = WorkOrder.objects.create(
            rentvine_id=100997,
            work_order_number="100997",
            description=WO_100997_RAW,
            portfolio=portfolio,
            property=prop,
            unit=unit,
        )
        d = _work_order_to_dict(wo)
        assert d["is_punch_list"] is True
        assert d["title"] == "Maintenance"
        assert d["description"] == "Inspection punch list - 5 items"

    def test_100988_punch_list(self, portfolio, prop, unit):
        from maintenance.models import WorkOrder
        from maintenance.selectors import _work_order_to_dict

        wo = WorkOrder.objects.create(
            rentvine_id=100988,
            work_order_number="100988",
            description=WO_100988_RAW,
            portfolio=portfolio,
            property=prop,
            unit=unit,
        )
        d = _work_order_to_dict(wo)
        assert d["is_punch_list"] is True
        assert d["title"] == "Repairs"
        assert d["description"] == "Inspection punch list - 32 items"

    def test_101081_single_item(self, portfolio, prop, unit):
        from maintenance.models import WorkOrder
        from maintenance.selectors import _work_order_to_dict

        wo = WorkOrder.objects.create(
            rentvine_id=101081,
            work_order_number="101081",
            description=WO_101081_RAW,
            portfolio=portfolio,
            property=prop,
            unit=unit,
        )
        d = _work_order_to_dict(wo)
        assert d["is_punch_list"] is False
        assert "Garbage to thrown out" in d["description"]
        assert "bamboo sprouts" in d["description"]
        assert "zInspector" not in d["description"]
        assert "Reference:" not in d["description"]
        assert "zinspector.com" not in d["description"]
        assert d["title"].startswith("Landscaping:")
        assert len(d["title"]) <= 80

    def test_101120_non_zinspector(self, portfolio, prop, unit):
        from maintenance.models import WorkOrder
        from maintenance.selectors import _work_order_to_dict

        wo = WorkOrder.objects.create(
            rentvine_id=101120,
            work_order_number="101120",
            description=WO_101120_RAW,
            portfolio=portfolio,
            property=prop,
            unit=unit,
        )
        d = _work_order_to_dict(wo)
        assert d["is_punch_list"] is False
        assert d["title"] == "Deep clean needed"
        assert "Requested by: Property Manager" in d["description"]
        # Standard dict shape unchanged
        expected_keys = {
            "id", "property_id", "work_order_number", "title", "description",
            "vendor_name", "is_owner_approved", "estimated_amount",
            "date_closed", "scheduled_start_date", "source_created_at",
            "source_modified_at", "unit_address", "property_address",
            "unit_label", "cost", "is_punch_list",
        }
        assert set(d.keys()) == expected_keys

    def test_malformed_zinspector_fallthrough(self, portfolio, prop, unit):
        from maintenance.models import WorkOrder
        from maintenance.selectors import _work_order_to_dict

        wo = WorkOrder.objects.create(
            rentvine_id=200002,
            work_order_number="200002",
            description=WO_100997_TRUNCATED_NO_INSTRUCTIONS,
            portfolio=portfolio,
            property=prop,
            unit=unit,
        )
        d = _work_order_to_dict(wo)
        assert d["is_punch_list"] is False
        # Falls through to _extract_title / _clean_html
        assert d["title"] == "Maintenance"
        assert "zInspector" in d["description"]
