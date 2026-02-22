from django.db.models import Count
from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.pagination import LimitOffsetPagination, paginate

from properties.models import MultifamilyProperty, Owner, Portfolio, Property, Unit
from properties.schemas import (
    MultifamilyPropertySchema,
    OwnerFilterSchema,
    OwnerListSchema,
    OwnerSchema,
    PortfolioFilterSchema,
    PortfolioListSchema,
    PortfolioSchema,
    PropertyFilterSchema,
    PropertyListSchema,
    PropertySchema,
    UnitFilterSchema,
    UnitListSchema,
    UnitSchema,
)

router = Router(tags=["Properties"])


# --- Portfolios ---


@router.get("/portfolios", response=list[PortfolioListSchema])
@paginate(LimitOffsetPagination)
def list_portfolios(request, filters: Query[PortfolioFilterSchema]):
    qs = Portfolio.objects.all()
    return filters.filter(qs)


@router.get("/portfolios/{portfolio_id}", response=PortfolioSchema)
def get_portfolio(request, portfolio_id: int):
    return get_object_or_404(Portfolio, id=portfolio_id)


# --- Owners ---


@router.get("/owners", response=list[OwnerListSchema])
@paginate(LimitOffsetPagination)
def list_owners(request, filters: Query[OwnerFilterSchema]):
    qs = Owner.objects.all()
    return filters.filter(qs)


@router.get("/owners/{owner_id}", response=OwnerSchema)
def get_owner(request, owner_id: int):
    return get_object_or_404(Owner, id=owner_id)


# --- Properties ---


@router.get("/properties", response=list[PropertyListSchema])
@paginate(LimitOffsetPagination)
def list_properties(request, filters: Query[PropertyFilterSchema]):
    qs = Property.objects.select_related("portfolio").annotate(
        unit_count=Count("units")
    )
    return filters.filter(qs)


@router.get("/properties/{property_id}", response=PropertySchema)
def get_property(request, property_id: int):
    return get_object_or_404(
        Property.objects.select_related("portfolio").annotate(
            unit_count=Count("units")
        ),
        id=property_id,
    )


@router.get("/properties/{property_id}/units", response=list[UnitListSchema])
@paginate(LimitOffsetPagination)
def list_property_units(request, property_id: int):
    get_object_or_404(Property, id=property_id)
    return Unit.objects.filter(property_id=property_id).select_related("property")


# --- Units ---


@router.get("/units", response=list[UnitListSchema])
@paginate(LimitOffsetPagination)
def list_units(request, filters: Query[UnitFilterSchema]):
    qs = Unit.objects.select_related("property")
    return filters.filter(qs)


@router.get("/units/{unit_id}", response=UnitSchema)
def get_unit(request, unit_id: int):
    return get_object_or_404(Unit.objects.select_related("property"), id=unit_id)


# --- MultifamilyProperties ---


@router.get("/multifamily-properties", response=list[MultifamilyPropertySchema])
@paginate(LimitOffsetPagination)
def list_multifamily_properties(request):
    return MultifamilyProperty.objects.all()


@router.get(
    "/multifamily-properties/{mf_id}", response=MultifamilyPropertySchema
)
def get_multifamily_property(request, mf_id: int):
    return get_object_or_404(MultifamilyProperty, id=mf_id)
