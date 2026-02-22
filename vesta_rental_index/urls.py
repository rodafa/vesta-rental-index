from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from ninja import NinjaAPI
from ninja.security import APIKeyHeader

from analytics.api import router as analytics_router
from leasing.api import router as leasing_router
from market.api import router as market_router
from properties.api import router as properties_router


class VestaAPIKey(APIKeyHeader):
    param_name = "X-API-Key"

    def authenticate(self, request, key):
        expected = settings.VESTA_API_KEY
        if not expected:
            return "dev"  # No key configured — allow all (local dev)
        if key == expected:
            return key
        return None


api = NinjaAPI(
    title="Vesta Rental Index API",
    version="0.1.0",
    description="Internal rental performance index for Vesta Property Management.",
    auth=[VestaAPIKey()],
)

api.add_router("/properties/", properties_router)
api.add_router("/leasing/", leasing_router)
api.add_router("/market/", market_router)
api.add_router("/analytics/", analytics_router)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
    path("", include("leasing.urls")),
]
