from django.contrib import admin

from .models import OwnerEmailSend, WeeklyReportRun


@admin.register(WeeklyReportRun)
class WeeklyReportRunAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "start_date",
        "end_date",
        "status",
        "units_processed",
        "notes_drafted",
        "units_skipped",
        "units_errored",
        "dry_run",
        "triggered_by",
        "started_at",
    ]
    list_filter = ["status", "dry_run"]
    search_fields = ["triggered_by"]
    readonly_fields = [
        "started_at",
        "completed_at",
        "error_log",
    ]
    actions = ["rerun"]

    @admin.action(description="Re-run selected weeks")
    def rerun(self, request, queryset):
        from .services.weekly_update import run_for_week

        for run in queryset:
            run_for_week(
                start_date=run.start_date,
                end_date=run.end_date,
                triggered_by=f"admin:{request.user.username}",
            )
        self.message_user(request, f"Re-ran {queryset.count()} update(s).")


@admin.register(OwnerEmailSend)
class OwnerEmailSendAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "owner",
        "week_date",
        "status",
        "sent_at",
        "sent_by",
        "sendgrid_message_id",
    ]
    list_filter = ["status", "week_date"]
    search_fields = ["owner__name", "owner__email"]
    readonly_fields = ["created_at", "updated_at", "units_included"]
