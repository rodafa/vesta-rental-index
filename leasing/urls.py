from django.urls import path

from . import views

urlpatterns = [
    path("rentengine/", views.rentengine_webhook, name="rentengine-webhook"),
    path("rentengine", views.rentengine_webhook),
    path(
        "rentvine/capture/<str:secret>/",
        views.rentvine_webhook_capture,
        name="rentvine-webhook-capture",
    ),
    path("rentvine/capture/<str:secret>", views.rentvine_webhook_capture),
]
