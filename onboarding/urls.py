from django.urls import path

from .views import minerva_events

urlpatterns = [
    path("slack/minerva/events/", minerva_events, name="minerva_events"),
]
