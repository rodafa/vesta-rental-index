from django.contrib import admin
from django.urls import path

from config.views import healthcheck

urlpatterns = [
    path("healthz", healthcheck),
    path("admin/", admin.site.urls),
]
