from django.urls import path

from utilities import views

urlpatterns = [
    path("", views.utility_grid, name="utility-grid"),
    path("export/", views.utility_grid_csv, name="utility-grid-csv"),
]
