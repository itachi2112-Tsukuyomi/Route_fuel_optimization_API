from django.urls import path

from . import views

urlpatterns = [
    path("route/", views.RouteFuelPlanView.as_view(), name="route-plan"),
    path("route/map/", views.route_map, name="route-map"),
]
