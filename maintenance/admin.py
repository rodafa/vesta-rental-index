from django.contrib import admin

from .models import Inspection, Vendor, VendorTrade, WorkOrder, WorkOrderStatus


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "phone", "is_active")
    search_fields = ("name", "email")
    list_filter = ("is_active",)


@admin.register(VendorTrade)
class VendorTradeAdmin(admin.ModelAdmin):
    list_display = ("name", "rentvine_id", "is_visible_tenant_portal")


@admin.register(WorkOrderStatus)
class WorkOrderStatusAdmin(admin.ModelAdmin):
    list_display = ("name", "primary_status", "is_system_status", "order_index")
    list_filter = ("primary_status",)


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = (
        "work_order_number", "property", "unit", "vendor",
        "primary_status", "priority", "estimated_amount",
    )
    search_fields = ("work_order_number", "property__address_line_1", "description")
    list_filter = ("primary_status", "priority", "source_type")


@admin.register(Inspection)
class InspectionAdmin(admin.ModelAdmin):
    list_display = ("unit", "inspection_type", "inspection_status", "scheduled_date", "inspection_date")
    list_filter = ("inspection_type", "inspection_status")
