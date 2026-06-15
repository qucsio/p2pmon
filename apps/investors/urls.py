from django.urls import path

from apps.investors import views

app_name = "investors"

urlpatterns = [
    path("", views.investor_list, name="list"),
    path("add/", views.investor_create, name="create"),
    path("<int:pk>/edit/", views.investor_edit, name="edit"),
    path("allocation/", views.calculate_allocation, name="allocation"),
]
