from django.contrib import admin

from .models import OwnerReportNote


@admin.register(OwnerReportNote)
class OwnerReportNoteAdmin(admin.ModelAdmin):
    list_display = ["owner", "report_date", "status", "updated_at"]
    list_filter = ["status", "report_date"]
