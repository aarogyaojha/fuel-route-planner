from typing import Any

from rest_framework import serializers

from apps.routing.services import Coordinate
from apps.stations.models import Station


class StationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Station
        fields = [
            "id",
            "opis_id",
            "name",
            "address",
            "city",
            "state",
            "rack_id",
            "retail_price",
            "latitude",
            "longitude",
        ]


class CoordinateSerializer(serializers.Serializer):
    latitude = serializers.FloatField(min_value=-90.0, max_value=90.0)
    longitude = serializers.FloatField(min_value=-180.0, max_value=180.0)


class LocationField(serializers.Field):
    """Field accepting either a coordinate mapping or a location query string."""

    def to_internal_value(self, data: Any) -> dict[str, float] | str:
        if isinstance(data, str):
            val = data.strip()
            if not val:
                raise serializers.ValidationError("Location string cannot be empty.")
            return val

        if isinstance(data, dict):
            if "latitude" not in data or "longitude" not in data:
                raise serializers.ValidationError(
                    "Coordinate dictionary must contain 'latitude' and 'longitude'."
                )
            try:
                lat = float(data["latitude"])
                lon = float(data["longitude"])
            except (ValueError, TypeError) as exc:
                raise serializers.ValidationError("Coordinates must be numeric.") from exc

            if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                raise serializers.ValidationError(
                    "Latitude must be between -90 and 90, longitude between -180 and 180."
                )
            return {"latitude": lat, "longitude": lon}

        raise serializers.ValidationError(
            "Location must be either a string or a coordinate object with latitude and longitude."
        )

    def to_representation(self, value: Any) -> dict[str, float] | str:
        if isinstance(value, Coordinate):
            return {"latitude": value.latitude, "longitude": value.longitude}
        return value


class RoutePlanRequestSerializer(serializers.Serializer):
    start = LocationField(required=True)
    finish = LocationField(required=False)
    end = LocationField(required=False)
    max_range_miles = serializers.FloatField(required=False, min_value=50.0, max_value=2000.0)
    mpg = serializers.FloatField(required=False, min_value=1.0, max_value=100.0)
    corridor_radius_miles = serializers.FloatField(required=False, min_value=1.0, max_value=100.0)
    min_purchase_gallons = serializers.FloatField(required=False, min_value=0.0)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if "finish" not in attrs and "end" not in attrs:
            raise serializers.ValidationError("Either 'finish' or 'end' must be provided.")
        if "finish" in attrs and "end" not in attrs:
            attrs["end"] = attrs["finish"]
        return attrs


class FuelStopSerializer(serializers.Serializer):
    station_id = serializers.IntegerField()
    opis_id = serializers.IntegerField()
    name = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    retail_price = serializers.DecimalField(max_digits=8, decimal_places=5)
    distance_along_route_miles = serializers.FloatField()
    gallons_refueled = serializers.FloatField()
    gallons_purchased = serializers.FloatField()
    price_per_gallon = serializers.DecimalField(max_digits=8, decimal_places=5)
    fuel_remaining_on_arrival = serializers.FloatField()
    cost = serializers.DecimalField(max_digits=10, decimal_places=2)


class RoutePlanResponseSerializer(serializers.Serializer):
    total_distance_miles = serializers.FloatField()
    total_duration_hours = serializers.FloatField()
    total_fuel_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_gallons_consumed = serializers.FloatField()
    total_gallons_purchased = serializers.FloatField()
    initial_fuel_cost = serializers.DecimalField(max_digits=12, decimal_places=2)
    initial_fuel_price_basis = serializers.DecimalField(max_digits=8, decimal_places=5)
    assumptions = serializers.DictField()
    fuel_stops = FuelStopSerializer(many=True)
    route = serializers.DictField()
    start = CoordinateSerializer()
    end = CoordinateSerializer()
