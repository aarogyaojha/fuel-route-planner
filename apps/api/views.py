from typing import Any

from django.db import connection
from rest_framework import status, viewsets
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from apps.api.serializers import (
    RoutePlanRequestSerializer,
    StationSerializer,
)
from apps.routing.services import (
    InvalidLocationError,
    NoFeasibleStopsError,
    NoRouteFound,
    RoutingUnavailable,
    plan_optimal_fuel_route,
)
from apps.stations.models import Station


class RoutePlanRateThrottle(SimpleRateThrottle):
    scope = "route_plan"
    rate = "60/minute"

    def get_cache_key(self, request: Request, view: Any) -> str:
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class HealthCheckView(APIView):
    def get(self, request: Request) -> Response:
        db_status = "connected"
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception:
            db_status = "disconnected"

        return Response(
            {
                "status": "healthy" if db_status == "connected" else "degraded",
                "database": db_status,
            },
            status=status.HTTP_200_OK
            if db_status == "connected"
            else status.HTTP_503_SERVICE_UNAVAILABLE,
        )


class StationViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Station.objects.all()
    serializer_class = StationSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        state = self.request.query_params.get("state")
        city = self.request.query_params.get("city")
        if state:
            qs = qs.filter(state__iexact=state.strip())
        if city:
            qs = qs.filter(city__icontains=city.strip())
        return qs


class RoutePlanView(APIView):
    throttle_classes = [RoutePlanRateThrottle]

    def post(self, request: Request) -> Response:
        serializer = RoutePlanRequestSerializer(data=request.data)
        if not serializer.is_valid():
            error_messages: list[str] = []
            for field, errs in serializer.errors.items():
                msg = ", ".join(str(e) for e in errs) if isinstance(errs, list) else str(errs)
                error_messages.append(f"{field}: {msg}")
            return Response(
                {"error": "; ".join(error_messages), "code": "INVALID_INPUT"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        validated: dict[str, Any] = serializer.validated_data

        try:
            plan_result = plan_optimal_fuel_route(
                start=validated["start"],
                end=validated["end"],
                max_range_miles=validated.get("max_range_miles"),
                mpg=validated.get("mpg"),
                corridor_radius_miles=validated.get("corridor_radius_miles"),
                min_purchase_gallons=validated.get("min_purchase_gallons"),
            )
        except InvalidLocationError as exc:
            return Response(
                {"error": exc.message, "code": exc.code},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except (NoRouteFound, NoFeasibleStopsError) as exc:
            return Response(
                {"error": exc.message, "code": exc.code},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except RoutingUnavailable as exc:
            return Response(
                {"error": exc.message, "code": exc.code},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(plan_result.to_dict(), status=status.HTTP_200_OK)
