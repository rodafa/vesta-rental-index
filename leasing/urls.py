from django.urls import path
from .views import show_me_leases
urlpatterns = [
    path("leasing/", show_me_leases),
]
