from django.contrib import admin

from .models import ExtractionRun, PropertyUtility, UnitNote, UtilityProposal


class ReadOnlyAdmin(admin.ModelAdmin):
    """Base admin with all mutations disabled."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ExtractionRun)
class ExtractionRunAdmin(ReadOnlyAdmin):
    list_display = [
        "pk", "status", "model_name", "started_at", "finished_at",
        "notes_sent", "notes_dropped", "triggered_by",
    ]
    list_filter = ["status"]


@admin.register(UtilityProposal)
class UtilityProposalAdmin(ReadOnlyAdmin):
    list_display = [
        "pk", "rentvine_property_id", "rentvine_unit_id",
        "utility_type", "responsibility", "basis",
        "status", "has_conflict", "created_at",
    ]
    list_filter = ["status", "utility_type", "responsibility", "basis", "has_conflict"]
    search_fields = ["rentvine_property_id", "rentvine_unit_id", "provider"]


@admin.register(PropertyUtility)
class PropertyUtilityAdmin(ReadOnlyAdmin):
    list_display = [
        "pk", "unit", "utility_type", "responsibility",
        "provider", "approved_by", "approved_at",
    ]
    list_filter = ["utility_type", "responsibility"]
    search_fields = ["provider"]


@admin.register(UnitNote)
class UnitNoteAdmin(ReadOnlyAdmin):
    list_display = [
        "pk", "unit", "utility_type", "text", "created_by", "created_at",
    ]
    list_filter = ["utility_type"]
    search_fields = ["text"]
