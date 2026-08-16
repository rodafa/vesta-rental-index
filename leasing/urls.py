from django.urls import path

from . import views

urlpatterns = [
    path("rentengine/", views.rentengine_webhook, name="rentengine-webhook"),
    path("rentengine", views.rentengine_webhook),
]
