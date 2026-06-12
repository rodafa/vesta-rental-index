"""URL patterns for the distribution send API."""

from django.urls import path

from . import distribution_api

urlpatterns = [
    path("recipients", distribution_api.list_recipients, name="distribution-sends-recipients"),
    path("recipients/<path:email>/preview", distribution_api.preview_recipient, name="distribution-sends-preview"),
    path("recipients/<path:email>/send", distribution_api.send_recipient, name="distribution-sends-send"),
    path("recipients/<path:email>/test-send", distribution_api.test_send_recipient, name="distribution-sends-test-send"),
]
