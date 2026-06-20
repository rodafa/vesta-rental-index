"""URL patterns for the maintenance-grain send API."""

from django.urls import path

from . import maintenance_api

urlpatterns = [
    path("recipients", maintenance_api.list_maintenance_recipients, name="maintenance-sends-recipients"),
    path("recipients/<path:email>/preview", maintenance_api.preview_maintenance_recipient, name="maintenance-sends-preview"),
    path("recipients/<path:email>/send", maintenance_api.send_maintenance_recipient, name="maintenance-sends-send"),
    path("recipients/<path:email>/test-send", maintenance_api.test_send_maintenance_recipient, name="maintenance-sends-test-send"),
    path("send-all", maintenance_api.send_all_maintenance_ready, name="maintenance-sends-send-all"),
]
