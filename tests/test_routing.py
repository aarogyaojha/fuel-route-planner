from decimal import Decimal
from unittest.mock import MagicMock

import pytest
import requests
from django.core.cache import cache

from apps.routing.services import (
    Coordinate,
    InvalidLocationError,
    NoFeasibleStopsError,
    NoRouteFound,
    RoutingUnavailable,
    fetch_route,
    geocode_location,
    haversine_distance_miles,
    plan_optimal_fuel_route,
)
from apps.stations.models import Station


@pytest.fixture(autouse=True)
def clear_django_cache():
    cache.clear()
    yield
    cache.clear()


def test_haversine_distance():
    dist = haversine_distance_miles(40.7128, -74.0060, 39.9526, -75.1652)
    assert 75.0 < dist < 85.0


def test_fetch_route_success(monkeypatch):
    start = Coordinate(latitude=40.7128, longitude=-74.0060)
    end = Coordinate(latitude=39.9526, longitude=-75.1652)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "routes": [
            {
                "summary": {"distance": 160934.0, "duration": 7200.0},
                "geometry": {"coordinates": [[-74.0060, 40.7128], [-75.1652, 39.9526]]},
            }
        ]
    }
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: mock_response)

    route = fetch_route(start, end)
    assert route["distance_miles"] > 0
    assert route["duration_hours"] > 0
    assert len(route["coordinates"]) == 2

    # Second call should come from cache (no post call needed)
    monkeypatch.setattr(requests, "post", MagicMock(side_effect=Exception("Should not be called")))
    cached_route = fetch_route(start, end)
    assert cached_route == route


def test_fetch_route_raises_routing_unavailable(monkeypatch):
    start = Coordinate(latitude=40.7128, longitude=-74.0060)
    end = Coordinate(latitude=39.9526, longitude=-75.1652)

    def mock_post_fail(*args, **kwargs):
        raise requests.RequestException("Connection timeout")

    monkeypatch.setattr(requests, "post", mock_post_fail)

    with pytest.raises(RoutingUnavailable):
        fetch_route(start, end)


def test_fetch_route_no_route_found(monkeypatch):
    start = Coordinate(latitude=40.7128, longitude=-74.0060)
    end = Coordinate(latitude=39.9526, longitude=-75.1652)

    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.json.return_value = {"error": "Route could not be found"}
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: mock_resp)

    with pytest.raises(NoRouteFound):
        fetch_route(start, end)


def test_geocode_location_success_and_caching(monkeypatch):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "features": [{"geometry": {"coordinates": [-87.6298, 41.8781]}}]
    }
    mock_get = MagicMock(return_value=mock_response)
    monkeypatch.setattr(requests, "get", mock_get)

    coord = geocode_location("Chicago, IL")
    assert coord.latitude == 41.8781
    assert coord.longitude == -87.6298
    assert mock_get.call_count == 1

    coord_cached = geocode_location("Chicago, IL")
    assert coord_cached == coord
    assert mock_get.call_count == 1


def test_geocode_location_outside_contiguous_us(monkeypatch):
    mock_response = MagicMock()
    mock_response.status_code = 200
    # Honolulu, HI coordinates (outside contiguous US)
    mock_response.json.return_value = {
        "features": [{"geometry": {"coordinates": [-157.8583, 21.3069]}}]
    }
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: mock_response)

    with pytest.raises(InvalidLocationError) as exc_info:
        geocode_location("Honolulu, HI")
    assert exc_info.value.code == "OUTSIDE_CONTIGUOUS_US"


@pytest.mark.django_db
def test_plan_optimal_fuel_route_short_distance(monkeypatch):
    start = Coordinate(latitude=40.7128, longitude=-74.0060)
    end = Coordinate(latitude=40.0, longitude=-74.0)

    mock_route = {
        "distance_miles": 100.0,
        "duration_hours": 2.0,
        "coordinates": [[40.7128, -74.0060], [40.0, -74.0]],
    }
    monkeypatch.setattr("apps.routing.services.fetch_route", lambda s, e: mock_route)

    plan = plan_optimal_fuel_route(start, end)
    assert plan.total_distance_miles < 500.0
    assert len(plan.fuel_stops) == 0
    assert plan.total_fuel_cost == Decimal("0.00")


@pytest.mark.django_db
def test_plan_optimal_fuel_route_requires_refueling(monkeypatch):
    start = Coordinate(latitude=40.7128, longitude=-74.0060)
    end = Coordinate(latitude=33.7490, longitude=-84.3880)

    mock_route = {
        "distance_miles": 800.0,
        "duration_hours": 14.5,
        "coordinates": [
            [40.7128, -74.0060],
            [38.0, -78.0],
            [35.0, -81.0],
            [33.7490, -84.3880],
        ],
    }
    monkeypatch.setattr("apps.routing.services.fetch_route", lambda s, e: mock_route)

    Station.objects.create(
        opis_id=2001,
        name="Shenandoah Fuel Plaza",
        address="100 Highway Rd",
        city="Staunton",
        state="VA",
        rack_id=1,
        retail_price=Decimal("2.99000"),
        latitude=38.0,
        longitude=-78.0,
    )

    plan = plan_optimal_fuel_route(
        start, end, max_range_miles=500.0, mpg=10.0, corridor_radius_miles=20.0
    )
    assert plan.total_distance_miles == 800.0
    assert len(plan.fuel_stops) >= 1
    assert plan.fuel_stops[0].name == "Shenandoah Fuel Plaza"
    assert plan.total_fuel_cost > Decimal("0.00")


@pytest.mark.django_db
def test_deterministic_short_trip_zero_stops(monkeypatch):
    start = Coordinate(latitude=40.7128, longitude=-74.0060)
    end = Coordinate(latitude=40.0, longitude=-74.0)

    mock_route = {
        "distance_miles": 200.0,
        "duration_hours": 3.0,
        "coordinates": [[40.7128, -74.0060], [40.0, -74.0]],
    }
    monkeypatch.setattr("apps.routing.services.fetch_route", lambda s, e: mock_route)

    st = Station.objects.create(
        opis_id=4001,
        name="Local Station",
        address="10 Local Rd",
        city="Newark",
        state="NJ",
        rack_id=1,
        retail_price=Decimal("3.20000"),
        latitude=40.5,
        longitude=-74.0,
    )

    def mock_candidates(coords, radius, total_dist=None):
        return [{"station": st, "route_mile": 25.0, "corridor_dist": 0.0}]

    monkeypatch.setattr("apps.routing.services.find_candidate_stations", mock_candidates)

    plan = plan_optimal_fuel_route(start, end)
    assert plan.total_distance_miles == 200.0
    assert len(plan.fuel_stops) == 0
    assert plan.total_gallons_purchased == 0.0
    assert plan.total_gallons_consumed == 20.0
    assert plan.initial_fuel_price_basis == Decimal("3.20000")
    assert plan.initial_fuel_cost == Decimal("64.00")
    assert plan.total_fuel_cost == Decimal("64.00")
    assert plan.assumptions["tank_capacity_gallons"] == 50.0
    assert plan.assumptions["mpg"] == 10.0
    assert plan.assumptions["max_range_miles"] == 500.0
    assert plan.assumptions["initial_fuel_price_basis_type"] == "cheapest_near_start"
    assert plan.route["type"] == "FeatureCollection"
    assert len(plan.route["features"]) == 3  # LineString + Start + Finish


@pytest.mark.django_db
def test_deterministic_last_stop_buys_remaining_gallons(monkeypatch):
    start = Coordinate(latitude=40.0, longitude=-88.0)
    end = Coordinate(latitude=40.0, longitude=-75.0)

    mock_route = {
        "distance_miles": 700.0,
        "duration_hours": 10.0,
        "coordinates": [[40.0, -88.0], [40.0, -80.0], [40.0, -75.0]],
    }
    monkeypatch.setattr("apps.routing.services.fetch_route", lambda s, e: mock_route)

    st1 = Station.objects.create(
        opis_id=5001,
        name="Mile 400 Stop",
        address="400 Highway Rd",
        city="Midway",
        state="PA",
        rack_id=1,
        retail_price=Decimal("3.50000"),
        latitude=40.0,
        longitude=-80.0,
    )

    def mock_candidates(coords, radius, total_dist=None):
        return [{"station": st1, "route_mile": 400.0, "corridor_dist": 0.0}]

    monkeypatch.setattr("apps.routing.services.find_candidate_stations", mock_candidates)

    plan = plan_optimal_fuel_route(start, end)

    assert plan.total_distance_miles == 700.0
    assert len(plan.fuel_stops) == 1
    stop = plan.fuel_stops[0]
    assert stop.distance_along_route_miles == 400.0
    assert stop.fuel_remaining_on_arrival == 10.0
    assert stop.gallons_purchased == 20.0
    assert stop.price_per_gallon == Decimal("3.50000")
    assert stop.cost == Decimal("70.00")
    assert plan.total_gallons_purchased == 20.0
    assert plan.initial_fuel_cost == Decimal("140.00")
    assert plan.initial_fuel_price_basis == Decimal("3.50000")
    assert plan.total_fuel_cost == Decimal("210.00")
    assert plan.route["type"] == "FeatureCollection"
    assert len(plan.route["features"]) == 4  # LineString + Start + Finish + 1 stop

    # Assert consecutive stops <= 500 miles apart
    pts = (
        [0.0]
        + [s.distance_along_route_miles for s in plan.fuel_stops]
        + [plan.total_distance_miles]
    )
    for p1, p2 in zip(pts[:-1], pts[1:], strict=True):
        assert p2 - p1 <= 500.0

    # Assert fuel never exceeds 50 gallons
    for s in plan.fuel_stops:
        assert s.fuel_remaining_on_arrival + s.gallons_purchased <= 50.0


@pytest.mark.django_db
def test_deterministic_cheaper_station_ahead_causes_partial_fill(monkeypatch):
    start = Coordinate(latitude=40.0, longitude=-88.0)
    end = Coordinate(latitude=40.0, longitude=-75.0)

    mock_route = {
        "distance_miles": 1000.0,
        "duration_hours": 15.0,
        "coordinates": [[40.0, -88.0], [40.0, -75.0]],
    }
    monkeypatch.setattr("apps.routing.services.fetch_route", lambda s, e: mock_route)

    st1 = Station.objects.create(
        opis_id=6001,
        name="Expensive Stop 1",
        address="300 Highway Rd",
        city="Staunton",
        state="VA",
        rack_id=1,
        retail_price=Decimal("4.00000"),
        latitude=40.0,
        longitude=-83.0,
    )
    st2 = Station.objects.create(
        opis_id=6002,
        name="Cheap Stop 2",
        address="600 Highway Rd",
        city="Knoxville",
        state="TN",
        rack_id=2,
        retail_price=Decimal("3.00000"),
        latitude=40.0,
        longitude=-80.0,
    )

    def mock_candidates(coords, radius, total_dist=None):
        return [
            {"station": st1, "route_mile": 300.0, "corridor_dist": 0.0},
            {"station": st2, "route_mile": 600.0, "corridor_dist": 0.0},
        ]

    monkeypatch.setattr("apps.routing.services.find_candidate_stations", mock_candidates)

    plan = plan_optimal_fuel_route(start, end)

    assert plan.total_distance_miles == 1000.0
    assert len(plan.fuel_stops) == 2

    # Stop 1 (mile 300): arrived with 20 gal, buys 10 gal to reach cheaper Stop 2 (300 mi away)
    stop1 = plan.fuel_stops[0]
    assert stop1.distance_along_route_miles == 300.0
    assert stop1.fuel_remaining_on_arrival == 20.0
    assert stop1.gallons_purchased == 10.0
    assert stop1.price_per_gallon == Decimal("4.00000")
    assert stop1.cost == Decimal("40.00")

    # Stop 2 (mile 600): arrived with 0 gal, buys 40 gal to reach destination (400 mi away)
    stop2 = plan.fuel_stops[1]
    assert stop2.distance_along_route_miles == 600.0
    assert stop2.fuel_remaining_on_arrival == 0.0
    assert stop2.gallons_purchased == 40.0
    assert stop2.price_per_gallon == Decimal("3.00000")
    assert stop2.cost == Decimal("120.00")

    assert plan.total_gallons_purchased == 50.0
    assert plan.initial_fuel_cost == Decimal("105.00")
    assert plan.initial_fuel_price_basis == Decimal("3.50000")
    assert plan.total_fuel_cost == Decimal("265.00")
    assert plan.route["type"] == "FeatureCollection"
    assert len(plan.route["features"]) == 5  # LineString + Start + Finish + 2 stops

    # Assert consecutive stops <= 500 miles apart
    pts = (
        [0.0]
        + [s.distance_along_route_miles for s in plan.fuel_stops]
        + [plan.total_distance_miles]
    )
    for p1, p2 in zip(pts[:-1], pts[1:], strict=True):
        assert p2 - p1 <= 500.0

    # Assert fuel never exceeds 50 gallons
    for s in plan.fuel_stops:
        assert s.fuel_remaining_on_arrival + s.gallons_purchased <= 50.0


@pytest.mark.django_db
def test_min_purchase_gallons_skips_small_stop_when_feasible(monkeypatch, settings):
    start = Coordinate(latitude=40.0, longitude=-88.0)
    end = Coordinate(latitude=40.0, longitude=-75.0)

    mock_route = {
        "distance_miles": 650.0,
        "duration_hours": 10.0,
        "coordinates": [[40.0, -88.0], [40.0, -75.0]],
    }
    monkeypatch.setattr("apps.routing.services.fetch_route", lambda s, e: mock_route)

    st1 = Station.objects.create(
        opis_id=7001,
        name="Station 1",
        address="300 Highway Rd",
        city="Staunton",
        state="VA",
        rack_id=1,
        retail_price=Decimal("4.00000"),
        latitude=40.0,
        longitude=-83.0,
    )
    st2 = Station.objects.create(
        opis_id=7002,
        name="Station 2",
        address="600 Highway Rd",
        city="Knoxville",
        state="TN",
        rack_id=2,
        retail_price=Decimal("3.00000"),
        latitude=40.0,
        longitude=-80.0,
    )

    def mock_candidates(coords, radius, total_dist=None):
        return [
            {"station": st1, "route_mile": 300.0, "corridor_dist": 0.0},
            {"station": st2, "route_mile": 600.0, "corridor_dist": 0.0},
        ]

    monkeypatch.setattr("apps.routing.services.find_candidate_stations", mock_candidates)

    # 1. Default behaviour: min_purchase_gallons = 0 -> both stops are included
    # Stop 1 (mile 300) buys 10 gal; Stop 2 (mile 600) buys 5 gal to reach destination at mile 650
    plan_default = plan_optimal_fuel_route(start, end)
    assert len(plan_default.fuel_stops) == 2
    assert plan_default.fuel_stops[0].gallons_purchased == 10.0
    assert plan_default.fuel_stops[1].gallons_purchased == 5.0

    # 2. When min_purchase_gallons = 8.0, Stop 2 (5 gal) is skipped because trip stays feasible
    # Station 1 (mile 300) can reach destination (mile 650) in 350 miles (<= 500 max range)
    plan_skipped = plan_optimal_fuel_route(start, end, min_purchase_gallons=8.0)
    assert len(plan_skipped.fuel_stops) == 1
    assert plan_skipped.fuel_stops[0].station_id == st1.id
    # Arrived at Station 1 with 20 gal, needs 35 gal to reach destination (350 miles), so buys 15 gal
    assert plan_skipped.fuel_stops[0].gallons_purchased == 15.0
    assert plan_skipped.total_distance_miles == 650.0

    # 3. Also verify setting via settings.MIN_PURCHASE_GALLONS
    settings.MIN_PURCHASE_GALLONS = 8.0
    plan_from_settings = plan_optimal_fuel_route(start, end)
    assert len(plan_from_settings.fuel_stops) == 1
    assert plan_from_settings.fuel_stops[0].station_id == st1.id
    assert plan_from_settings.fuel_stops[0].gallons_purchased == 15.0


@pytest.mark.django_db
def test_min_purchase_gallons_does_not_skip_when_infeasible(monkeypatch):
    start = Coordinate(latitude=40.0, longitude=-88.0)
    end = Coordinate(latitude=40.0, longitude=-75.0)

    # Total distance is 850 miles. Station 1 at 300, Station 2 at 600.
    # Without Station 2, Station 1 cannot reach destination (850 - 300 = 550 > 500 max range).
    # Station 2 buys 25 gal to reach destination (850 - 600 = 250 miles).
    mock_route = {
        "distance_miles": 850.0,
        "duration_hours": 13.0,
        "coordinates": [[40.0, -88.0], [40.0, -75.0]],
    }
    monkeypatch.setattr("apps.routing.services.fetch_route", lambda s, e: mock_route)

    st1 = Station.objects.create(
        opis_id=7101,
        name="Station 1 Infeasible",
        address="300 Highway Rd",
        city="Staunton",
        state="VA",
        rack_id=1,
        retail_price=Decimal("4.00000"),
        latitude=40.0,
        longitude=-83.0,
    )
    st2 = Station.objects.create(
        opis_id=7102,
        name="Station 2 Infeasible",
        address="600 Highway Rd",
        city="Knoxville",
        state="TN",
        rack_id=2,
        retail_price=Decimal("3.00000"),
        latitude=40.0,
        longitude=-80.0,
    )

    def mock_candidates(coords, radius, total_dist=None):
        return [
            {"station": st1, "route_mile": 300.0, "corridor_dist": 0.0},
            {"station": st2, "route_mile": 600.0, "corridor_dist": 0.0},
        ]

    monkeypatch.setattr("apps.routing.services.find_candidate_stations", mock_candidates)

    # Stop 1 buys 10 gal (under threshold 15.0), but skipping Stop 1 is impossible (can't reach Stop 2 at 600 mi).
    # Stop 1 MUST be retained because skipping it makes trip infeasible.
    plan = plan_optimal_fuel_route(start, end, min_purchase_gallons=15.0)
    assert len(plan.fuel_stops) == 2
    assert plan.fuel_stops[0].station_id == st1.id
    assert plan.fuel_stops[0].gallons_purchased == 10.0


@pytest.mark.django_db
def test_deterministic_infeasible_gap(monkeypatch):
    start = Coordinate(latitude=40.0, longitude=-88.0)
    end = Coordinate(latitude=40.0, longitude=-75.0)

    mock_route = {
        "distance_miles": 1200.0,
        "duration_hours": 18.0,
        "coordinates": [[40.0, -88.0], [40.0, -75.0]],
    }
    monkeypatch.setattr("apps.routing.services.fetch_route", lambda s, e: mock_route)

    st1 = Station.objects.create(
        opis_id=7001,
        name="Stop 1",
        address="300 Highway Rd",
        city="City A",
        state="OH",
        rack_id=1,
        retail_price=Decimal("3.00000"),
        latitude=40.0,
        longitude=-83.0,
    )
    st2 = Station.objects.create(
        opis_id=7002,
        name="Stop 2",
        address="900 Highway Rd",
        city="City B",
        state="IL",
        rack_id=2,
        retail_price=Decimal("3.00000"),
        latitude=40.0,
        longitude=-80.0,
    )

    # Gap between 300 and 900 is 600 miles (> 500 miles)
    def mock_candidates(coords, radius, total_dist=None):
        return [
            {"station": st1, "route_mile": 300.0, "corridor_dist": 0.0},
            {"station": st2, "route_mile": 900.0, "corridor_dist": 0.0},
        ]

    monkeypatch.setattr("apps.routing.services.find_candidate_stations", mock_candidates)

    with pytest.raises(NoFeasibleStopsError) as exc_info:
        plan_optimal_fuel_route(start, end)
    assert exc_info.value.code == "NO_FEASIBLE_STOPS"


@pytest.mark.django_db
def test_initial_fuel_fallback_to_corridor_average(monkeypatch):
    start = Coordinate(latitude=40.0, longitude=-88.0)
    end = Coordinate(latitude=40.0, longitude=-85.0)

    mock_route = {
        "distance_miles": 150.0,
        "duration_hours": 2.5,
        "coordinates": [[40.0, -88.0], [40.0, -85.0]],
    }
    monkeypatch.setattr("apps.routing.services.fetch_route", lambda s, e: mock_route)

    # Stations at mile 80 and 120 (both > 50 miles from start)
    st1 = Station.objects.create(
        opis_id=7101,
        name="Far Station 1",
        address="80 Highway Rd",
        city="City A",
        state="IL",
        rack_id=1,
        retail_price=Decimal("3.20000"),
        latitude=40.0,
        longitude=-86.5,
    )
    st2 = Station.objects.create(
        opis_id=7102,
        name="Far Station 2",
        address="120 Highway Rd",
        city="City B",
        state="IN",
        rack_id=2,
        retail_price=Decimal("3.60000"),
        latitude=40.0,
        longitude=-85.5,
    )

    def mock_candidates(coords, radius, total_dist=None):
        return [
            {"station": st1, "route_mile": 80.0, "corridor_dist": 0.0},
            {"station": st2, "route_mile": 120.0, "corridor_dist": 0.0},
        ]

    monkeypatch.setattr("apps.routing.services.find_candidate_stations", mock_candidates)

    plan = plan_optimal_fuel_route(start, end)

    assert plan.total_distance_miles == 150.0
    assert len(plan.fuel_stops) == 0
    assert plan.total_gallons_consumed == 15.0
    # Average of 3.20 and 3.60 is 3.40
    assert plan.initial_fuel_price_basis == Decimal("3.40000")
    assert plan.initial_fuel_cost == Decimal("51.00")
    assert plan.total_fuel_cost == Decimal("51.00")
    assert plan.assumptions["initial_fuel_price_basis_type"] == "corridor_average"
