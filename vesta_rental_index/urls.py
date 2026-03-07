from django.conf import settings
from django.contrib import admin
from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordChangeView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.db import connection
from django.urls import include, path
from django.views.generic import RedirectView
from ninja import NinjaAPI
from ninja.security import APIKeyCookie, APIKeyHeader

from analytics.api import router as analytics_router
from dashboard.api import router as dashboard_api_router
from dashboard.views import PasswordChangeCompleteView, owner_dashboard
from integrations.api import router as webhooks_router
from integrations.pipeline_api import router as pipeline_router
from leasing.api import router as leasing_router
from market.api import router as market_router
from properties.api import router as properties_router
from screening.api import router as screening_router


class VestaAPIKey(APIKeyHeader):
    param_name = "X-API-Key"

    def authenticate(self, request, key):
        expected = settings.VESTA_API_KEY
        if not expected:
            return "dev"  # No key configured — allow all (local dev)
        if key == expected:
            return key
        return None


class SessionAuth(APIKeyCookie):
    """Allow access via Django session cookie (for logged-in dashboard users)."""

    param_name = "sessionid"

    def __init__(self):
        super().__init__(csrf=False)

    def authenticate(self, request, key):
        if request.user and request.user.is_authenticated:
            return request.user
        return None


api = NinjaAPI(
    title="Vesta Rental Index API",
    version="0.1.0",
    description="Internal rental performance index for Vesta Property Management.",
    auth=[VestaAPIKey(), SessionAuth()],
)


@api.get("/health", auth=None, tags=["System"])
def health_check(request):
    try:
        connection.ensure_connection()
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        return {"status": "error", "db": str(e)}


api.add_router("/properties/", properties_router)
api.add_router("/leasing/", leasing_router)
api.add_router("/market/", market_router)
api.add_router("/analytics/", analytics_router)
api.add_router("/dashboard/", dashboard_api_router)
api.add_router("/webhooks/", webhooks_router)
api.add_router("/screening/", screening_router)
api.add_router("/pipeline/", pipeline_router)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", LoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(next_page="/accounts/login/"), name="logout"),
    path(
        "accounts/password-change/",
        PasswordChangeView.as_view(
            template_name="registration/password_change.html",
            success_url="/accounts/password-change/done/",
        ),
        name="password_change",
    ),
    path(
        "accounts/password-change/done/",
        PasswordChangeCompleteView.as_view(),
        name="password_change_done",
    ),
    path(
        "accounts/password-reset/",
        PasswordResetView.as_view(
            template_name="registration/password_reset_form.html",
            email_template_name="registration/password_reset_email.html",
            success_url="/accounts/password-reset/done/",
        ),
        name="password_reset",
    ),
    path(
        "accounts/password-reset/done/",
        PasswordResetDoneView.as_view(
            template_name="registration/password_reset_done.html",
        ),
        name="password_reset_done",
    ),
    path(
        "accounts/password-reset/confirm/<uidb64>/<token>/",
        PasswordResetConfirmView.as_view(
            template_name="registration/password_reset_confirm.html",
            success_url="/accounts/password-reset/complete/",
        ),
        name="password_reset_confirm",
    ),
    path(
        "accounts/password-reset/complete/",
        PasswordResetCompleteView.as_view(
            template_name="registration/password_reset_complete.html",
        ),
        name="password_reset_complete",
    ),
    path("api/", api.urls),
    path("dashboard/", include("dashboard.urls")),
    path("owner/<slug:portfolio_slug>/", owner_dashboard, name="owner_dashboard"),
    path("", RedirectView.as_view(url="/dashboard/", permanent=False)),
]
