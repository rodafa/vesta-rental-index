from django.contrib import admin

from .models import OwnerOnboard


@admin.register(OwnerOnboard)
class OwnerOnboardAdmin(admin.ModelAdmin):
    list_display = ["property_address", "owner1_name", "plan", "occupied", "submitted_at"]
    list_filter = ["plan", "occupied"]
    search_fields = ["property_address", "owner1_name", "owner1_email"]
    readonly_fields = ["submitted_at"]
