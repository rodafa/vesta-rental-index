from django.contrib import admin

from .models import OwnerReportNote, UnitNote


@admin.register(OwnerReportNote)
class OwnerReportNoteAdmin(admin.ModelAdmin):
    list_display = ["owner", "report_date", "status", "updated_at"]
    list_filter = ["status", "report_date"]


@admin.register(UnitNote)
class UnitNoteAdmin(admin.ModelAdmin):
    list_display = ["unit", "author", "created_at"]
    list_filter = ["author"]
    search_fields = ["note_text", "author"]
