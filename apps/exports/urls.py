from django.urls import path

from apps.exports import views

app_name = "exports"

urlpatterns = [
    path("orders/", views.orders_export, name="orders"),
]
