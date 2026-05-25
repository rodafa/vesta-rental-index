from django.contrib import admin

from .models import Inspection, Meld, Vendor, VendorTrade


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "phone", "is_active")
    search_fields = ("name", "email")
    list_filter = ("is_active",)


@admin.register(VendorTrade)
class VendorTradeAdmin(admin.ModelAdmin):
    list_display = ("name", "rentvine_id", "is_visible_tenant_portal")



@admin.register(Inspection)
class InspectionAdmin(admin.ModelAdmin):
    list_display = ("unit", "inspection_type", "inspection_status", "scheduled_date", "inspection_date")
    list_filter = ("inspection_type", "inspection_status")


@admin.register(Meld)
class MeldAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "brief_description",
        "status",
        "priority",
        "assigned_vendor_name",
        "coordinator_name",
        "scheduled_date",
        "has_invoice",
        "owner_approval_status",
    )
    search_fields = (
        "property_meld_id",
        "brief_description",
        "assigned_vendor_name",
        "coordinator_name",
        "property_address",
    )
    list_filter = ("status", "priority", "owner_approval_status", "has_invoice")
