from django.urls import path

from keys.views import slack_events

urlpatterns = [
    path("events/", slack_events, name="keybot-events"),
]
