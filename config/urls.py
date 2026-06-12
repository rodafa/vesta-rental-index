from django.contrib import admin
from django.urls import include, path

from comms import api as comms_api
from comms import distribution_api
from comms import portfolio_api as comms_portfolio_api
from comms.views import monthly_notes_page
from config.views import healthcheck

urlpatterns = [
    path("healthz", healthcheck),
    path("admin/", admin.site.urls),
    # Monthly notes API — list endpoint at the root (no trailing slash)
    path("api/reports/owner-notes", comms_api.list_notes, name="notes-list"),
    # Sub-endpoints with trailing slash from the include
    path("api/reports/owner-notes/", include("comms.api_urls")),
    # Portfolio-grain notes API (new — Layer 1 authoring)
    path("api/reports/portfolio-notes", comms_portfolio_api.list_portfolio_notes, name="portfolio-notes-list"),
    path("api/reports/portfolio-notes/", include("comms.portfolio_api_urls")),
    # Owner-grain send API (new — Layer 2 assembled sends)
    path("api/reports/owner-sends/", include("comms.owner_sends_urls")),
    # Distribution snapshots + send API
    path("api/reports/distribution-snapshots", distribution_api.list_snapshots, name="distribution-snapshots"),
    path("api/reports/distribution-sends/", include("comms.distribution_sends_urls")),
    path("api/reports/sync-statements", comms_api.sync_statements, name="sync-statements"),
    path("dashboard/monthly-notes/", monthly_notes_page, name="monthly-notes"),
]
