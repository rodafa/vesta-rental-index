from django.contrib import admin

from .models import APISyncLog


@admin.register(APISyncLog)
class APISyncLogAdmin(admin.ModelAdmin):
    list_display = ("source", "endpoint", "status", "records_fetched", "records_created", "started_at")
    list_filter = ("source", "status")
    search_fields = ("endpoint",)
    readonly_fields = ("started_at",)
