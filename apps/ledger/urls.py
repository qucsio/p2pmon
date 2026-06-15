from django.urls import path

from apps.ledger import views

app_name = "ledger"

urlpatterns = [
    path("", views.adjustment_list, name="adjustment_list"),
    path("add/", views.adjustment_create, name="adjustment_create"),
    path("<int:pk>/edit/", views.adjustment_edit, name="adjustment_edit"),
    path("<int:pk>/delete/", views.adjustment_delete, name="adjustment_delete"),
]
