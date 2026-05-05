from django.contrib import admin

from .models import (
    Applicant,
    Application,
    DailyLeasingMetric,
    Lease,
    LeasingEvent,
    Prospect,
    Showing,
    ShowingFeedback,
    Tenant,
)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "phone", "is_active")
    search_fields = ("name", "email")
    list_filter = ("is_active",)


class ApplicantInline(admin.TabularInline):
    model = Applicant
    extra = 0


@admin.register(Lease)
class LeaseAdmin(admin.ModelAdmin):
    list_display = (
        "rentvine_id", "property", "unit",
        "primary_lease_status", "rent_amount", "start_date", "end_date", "is_renewal",
    )
    search_fields = ("rentvine_id", "property__address_line_1", "unit__address_line_1")
    list_filter = ("primary_lease_status", "is_renewal")


@admin.register(Prospect)
class ProspectAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "source", "status", "unit_of_interest")
    search_fields = ("name", "email")
    list_filter = ("source", "status")


@admin.register(LeasingEvent)
class LeasingEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "prospect", "unit", "event_date")
    search_fields = ("prospect__name",)
    list_filter = ("event_type", "event_date")
    date_hierarchy = "event_date"


@admin.register(Showing)
class ShowingAdmin(admin.ModelAdmin):
    list_display = ("prospect", "unit", "showing_method", "status", "scheduled_at")
    list_filter = ("status", "showing_method")


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("number", "unit", "primary_status", "city", "state")
    search_fields = ("number",)
    list_filter = ("primary_status",)
    inlines = [ApplicantInline]


@admin.register(Applicant)
class ApplicantAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "application")
    search_fields = ("name", "email")


# --- Leasing Directory ---


class ShowingFeedbackInline(admin.TabularInline):
    model = ShowingFeedback
    extra = 0
    readonly_fields = (
        "rentengine_prospect_id",
        "prospect_name",
        "showing_completed_at",
        "feedback",
    )

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(DailyLeasingMetric)
class DailyLeasingMetricAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "unit",
        "new_prospects",
        "showings_completed",
        "showings_missed_or_failed",
        "applications_submitted",
        "property_health",
        "days_on_market",
        "source",
    )
    list_filter = ("source", "property_health", "date")
    search_fields = (
        "unit__name",
        "unit__address_line_1",
        "unit__property__address_line_1",
    )
    date_hierarchy = "date"
    readonly_fields = ("created_at", "updated_at", "raw_payload")
    list_per_page = 50
    list_select_related = ("unit__property",)


@admin.register(ShowingFeedback)
class ShowingFeedbackAdmin(admin.ModelAdmin):
    list_display = (
        "prospect_name",
        "unit",
        "showing_completed_at",
        "feedback_preview",
    )
    list_filter = ("showing_completed_at",)
    search_fields = ("prospect_name", "unit__address_line_1")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("unit__property",)

    def feedback_preview(self, obj):
        if not obj.feedback:
            return "-"
        return obj.feedback[:80] + ("..." if len(obj.feedback) > 80 else "")

    feedback_preview.short_description = "Feedback"
