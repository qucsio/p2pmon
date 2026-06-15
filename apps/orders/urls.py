from django.urls import path

from apps.orders import views

app_name = "orders"

urlpatterns = [
    path("", views.order_list, name="list"),
    path("<int:pk>/ignore/", views.order_ignore, name="ignore"),
    path("<int:pk>/restore/", views.order_restore, name="restore"),
    path("<int:pk>/raw/", views.order_raw_json, name="raw_json"),
]
