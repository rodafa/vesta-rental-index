"""URL patterns for the owner-grain send API."""

from django.urls import path

from . import portfolio_api

urlpatterns = [
    path("recipients", portfolio_api.list_recipients, name="owner-sends-recipients"),
    path("recipients/<path:email>/preview", portfolio_api.preview_recipient, name="owner-sends-preview"),
    path("recipients/<path:email>/send", portfolio_api.send_recipient, name="owner-sends-send"),
    path("recipients/<path:email>/test-send", portfolio_api.test_send_recipient, name="owner-sends-test-send"),
    path("send-all", portfolio_api.send_all_ready, name="owner-sends-send-all"),
]
