from django.contrib import admin

from .models import APISyncLog, WebhookEvent


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("id", "source", "table_name", "event_type", "processed", "processing_error_short", "received_at")
    list_filter = ("source", "processed", "table_name", "event_type")
    search_fields = ("table_name", "event_type", "processing_error")
    readonly_fields = ("source", "event_type", "table_name", "record", "old_record", "processed", "processed_at", "processing_error", "received_at")
    ordering = ("-received_at",)

    @admin.display(description="Error")
    def processing_error_short(self, obj):
        if not obj.processing_error:
            return ""
        return obj.processing_error[:80] + "..." if len(obj.processing_error) > 80 else obj.processing_error


@admin.register(APISyncLog)
class APISyncLogAdmin(admin.ModelAdmin):
    list_display = ("source", "endpoint", "sync_type", "status", "records_fetched", "records_created", "started_at")
    list_filter = ("source", "status")
    search_fields = ("endpoint",)
