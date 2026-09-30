from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.api.views import HealthCheckView, RoutePlanView, StationViewSet

router = DefaultRouter()
router.register("stations", StationViewSet, basename="station")

urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="health-check"),
    path("route/plan/", RoutePlanView.as_view(), name="route-plan"),
    path("", include(router.urls)),
]
