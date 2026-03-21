from django.urls import path

from .views import slack_events

urlpatterns = [
    path("slack/events/", slack_events, name="slack_events"),
]
