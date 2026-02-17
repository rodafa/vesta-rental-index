from django.contrib import admin

from .models import APISyncLog, WebhookEvent


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("source", "table_name", "event_type", "processed", "received_at")
    list_filter = ("source", "processed", "table_name")
    search_fields = ("table_name",)


@admin.register(APISyncLog)
class APISyncLogAdmin(admin.ModelAdmin):
    list_display = ("source", "endpoint", "sync_type", "status", "records_fetched", "records_created", "started_at")
    list_filter = ("source", "status")
    search_fields = ("endpoint",)
