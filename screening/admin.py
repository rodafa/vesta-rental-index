from django.contrib import admin

from .models import ScreeningApplication, ScreeningReport


@admin.register(ScreeningApplication)
class ScreeningApplicationAdmin(admin.ModelAdmin):
    list_display = ("applicant_name", "applicant_email", "status", "unit", "submitted_at")
    search_fields = ("applicant_name", "applicant_email", "boompay_id")
    list_filter = ("status",)


@admin.register(ScreeningReport)
class ScreeningReportAdmin(admin.ModelAdmin):
    list_display = ("screening_application", "report_type", "decision", "completed_at")
    list_filter = ("report_type", "decision")
