from django.contrib import admin
from django.urls import include, path

from comms import api as comms_api
from comms.views import monthly_notes_page
from config.views import healthcheck

urlpatterns = [
    path("healthz", healthcheck),
    path("admin/", admin.site.urls),
    # Monthly notes API — list endpoint at the root (no trailing slash)
    path("api/reports/owner-notes", comms_api.list_notes, name="notes-list"),
    # Sub-endpoints with trailing slash from the include
    path("api/reports/owner-notes/", include("comms.api_urls")),
    path("dashboard/monthly-notes/", monthly_notes_page, name="monthly-notes"),
]
