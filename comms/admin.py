from django.contrib import admin

from .models import (
    PortfolioDistributionSnapshot,
    PortfolioMaintenanceNote,
    PortfolioMonthlyNote,
    VoiceGuide,
)


@admin.register(VoiceGuide)
class VoiceGuideAdmin(admin.ModelAdmin):
    list_display = ("product", "updated_at")
    readonly_fields = ("updated_at",)


@admin.register(PortfolioMonthlyNote)
class PortfolioMonthlyNoteAdmin(admin.ModelAdmin):
    list_display = ("portfolio", "period_type", "period_start", "status", "updated_at")
    list_filter = ("status", "period_type")
    search_fields = ("portfolio__name",)
    readonly_fields = ("generated_at", "updated_at")


@admin.register(PortfolioMaintenanceNote)
class PortfolioMaintenanceNoteAdmin(admin.ModelAdmin):
    list_display = ("portfolio", "period_type", "period_start", "status", "updated_at")
    list_filter = ("status", "period_type")
    search_fields = ("portfolio__name",)
    readonly_fields = ("generated_at", "updated_at", "notes_html", "generated_note")
    fields = (
        "portfolio",
        "period_type",
        "period_start",
        "period_end",
        "status",
        "generated_note",
        "approved_generated_note",
        "notes_html",
        "generated_at",
        "updated_at",
    )


@admin.register(PortfolioDistributionSnapshot)
class PortfolioDistributionSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "portfolio",
        "period_type",
        "period_start",
        "distribution_amount",
        "distribution_date",
        "updated_at",
    )
    list_filter = ("period_type",)
    search_fields = ("portfolio__name",)
    readonly_fields = ("generated_at", "updated_at")
