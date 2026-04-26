from django.contrib import admin

from .models import OwnerReportLog


@admin.register(OwnerReportLog)
class OwnerReportLogAdmin(admin.ModelAdmin):
    list_display = ["owner_name", "property_address", "report_month", "status", "created_at"]
    list_filter = ["status", "report_month"]
    search_fields = ["owner_name", "property_address", "owner_id"]
    readonly_fields = ["created_at"]
