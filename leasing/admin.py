from django.contrib import admin

from .models import Applicant, Application, Lease, LeasingEvent, Prospect, Showing, Tenant


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
        "primary_lease_status", "start_date", "end_date", "is_renewal",
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
