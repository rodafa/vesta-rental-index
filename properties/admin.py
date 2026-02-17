from django.contrib import admin

from .models import Floorplan, MultifamilyProperty, Owner, Portfolio, Property, Unit


@admin.register(Portfolio)
class PortfolioAdmin(admin.ModelAdmin):
    list_display = ("name", "rentvine_id", "is_active", "reserve_amount")
    search_fields = ("name",)
    list_filter = ("is_active",)


@admin.register(Owner)
class OwnerAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "phone", "is_active")
    search_fields = ("name", "email")
    list_filter = ("is_active",)


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = (
        "address_line_1", "city", "state", "property_type",
        "service_type", "portfolio", "is_active",
    )
    search_fields = ("address_line_1", "city", "name")
    list_filter = ("property_type", "service_type", "state", "is_active", "portfolio")


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = (
        "address_line_1", "property", "bedrooms",
        "full_bathrooms", "square_feet", "target_rental_rate", "is_active",
    )
    search_fields = ("address_line_1", "name", "property__address_line_1")
    list_filter = ("bedrooms", "is_active", "property__city")


@admin.register(MultifamilyProperty)
class MultifamilyPropertyAdmin(admin.ModelAdmin):
    list_display = ("name", "text_address", "rentengine_id")
    search_fields = ("name", "text_address")


@admin.register(Floorplan)
class FloorplanAdmin(admin.ModelAdmin):
    list_display = ("name", "multifamily_property", "rentengine_id")
    search_fields = ("name",)
