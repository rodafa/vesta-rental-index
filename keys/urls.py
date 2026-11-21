from django.urls import path

from keys.views import slack_events, slack_interactions

urlpatterns = [
    path("events/", slack_events, name="keybot-events"),
    path("interactions/", slack_interactions, name="keybot-interactions"),
]
