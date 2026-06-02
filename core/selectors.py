"""
Core read queries. All data retrieval logic for core models lives here.
"""

from core.models import Unit


def get_revenue_units():
    """Active units excluding non-revenue (storage, garages, etc.)."""
    return Unit.objects.filter(is_active=True).exclude(
        raw_data__contains={"unit": {"isNonRevenue": "1"}}
    )
