from django.contrib import admin

from .models import Lease, Owner, Portfolio, Property, Tenant, Unit


@admin.register(Portfolio)
class PortfolioAdmin(admin.ModelAdmin):
    list_display = ("name", "rentvine_id", "is_active")
    search_fields = ("name",)
    list_filter = ("is_active",)


@admin.register(Owner)
class OwnerAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "rentvine_contact_id", "is_active")
    search_fields = ("name", "email")
    list_filter = ("is_active",)


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ("name", "address_line_1", "city", "state", "service_type", "is_active")
    search_fields = ("name", "address_line_1")
    list_filter = ("service_type", "property_type", "is_active")


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("__str__", "property", "bedrooms", "is_active")
    search_fields = ("name", "address_line_1")
    list_filter = ("is_active",)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "rentvine_contact_id", "is_active")
    search_fields = ("name", "email")
    list_filter = ("is_active",)


@admin.register(Lease)
class LeaseAdmin(admin.ModelAdmin):
    list_display = ("__str__", "primary_lease_status", "start_date", "end_date")
    search_fields = ("rentvine_id",)
    list_filter = ("primary_lease_status",)
