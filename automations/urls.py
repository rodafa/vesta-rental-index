from django.urls import path

from . import views

urlpatterns = [
    path("submit/", views.onboard_submit, name="onboard-submit"),
]
