"""
Tests for the canonical unit detail URL.

Verifies:
- Unit.get_absolute_url() resolves correctly.
- The canonical view returns 200 for an authenticated user.
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from properties.models import Portfolio, Property, Unit

User = get_user_model()


@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        username="teststaff",
        password="testpass123",
        is_staff=True,
    )


@pytest.fixture
def portfolio(db):
    return Portfolio.objects.create(rentvine_id=9999, name="Test Portfolio")


@pytest.fixture
def prop(portfolio):
    return Property.objects.create(
        rentvine_id=8888,
        portfolio=portfolio,
        name="123 Main St",
        address_line_1="123 Main St",
        city="Asheville",
        state="NC",
        postal_code="28801",
    )


@pytest.fixture
def unit(prop):
    return Unit.objects.create(
        property=prop,
        rentvine_id=7777,
        name="",
        address_line_1="123 Main St",
        city="Asheville",
        state="NC",
        postal_code="28801",
    )


class TestUnitCanonicalURL:
    def test_get_absolute_url_returns_correct_path(self, unit):
        url = unit.get_absolute_url()
        assert url == f"/dashboard/units/{unit.pk}/"

    def test_get_absolute_url_matches_reverse(self, unit):
        url = unit.get_absolute_url()
        expected = reverse("dashboard:unit_detail", kwargs={"pk": unit.pk})
        assert url == expected

    @override_settings(
        STORAGES={
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
            },
        }
    )
    def test_canonical_view_returns_200(self, client, staff_user, unit):
        client.force_login(staff_user)
        url = unit.get_absolute_url()
        response = client.get(url)
        assert response.status_code == 200

    @override_settings(
        STORAGES={
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
            },
        }
    )
    def test_canonical_view_requires_login(self, client, unit):
        url = unit.get_absolute_url()
        response = client.get(url)
        # login_required redirects to login page
        assert response.status_code == 302
        assert "/login" in response.url or "/accounts/login" in response.url
