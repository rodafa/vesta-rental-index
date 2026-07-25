from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import include, path

from comms import api as comms_api
from comms import distribution_api
from comms import maintenance_api as comms_maintenance_api
from comms import portfolio_api as comms_portfolio_api
from comms.views import maintenance_notes_page, monthly_notes_page
from config.views import healthcheck

urlpatterns = [
    path("healthz", healthcheck),
    path("admin/", admin.site.urls),
    # Auth — only login + logout (no password-reset dead routes)
    path("accounts/login/", LoginView.as_view(
        template_name="registration/login.html",
        redirect_authenticated_user=True,
    ), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
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
    # Maintenance notes API (Layer 1 authoring)
    path("api/reports/maintenance-notes", comms_maintenance_api.list_maintenance_notes, name="maintenance-notes-list"),
    path("api/reports/maintenance-notes/", include("comms.maintenance_api_urls")),
    # Maintenance sends API (Layer 2 assembled sends)
    path("api/reports/maintenance-sends/", include("comms.maintenance_sends_urls")),
    path("dashboard/maintenance-notes/", maintenance_notes_page, name="maintenance-notes"),
    # Automations — onboarding webhook
    path("api/onboard/", include("automations.urls")),
]
