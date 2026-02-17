from django.contrib import admin
from django.urls import include, path
from ninja import NinjaAPI

api = NinjaAPI(
    title="Vesta Rental Index API",
    version="0.1.0",
    description="Internal rental performance index for Vesta Property Management.",
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
    path("", include("leasing.urls")),
]
