from decimal import Decimal
from unittest.mock import MagicMock

import pytest
import requests
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APIClient

from apps.stations.models import Station


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture(autouse=True)
def clear_django_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_health_check_endpoint(api_client: APIClient):
    response = api_client.get("/api/v1/health/")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == "healthy"
    assert response.json()["database"] == "connected"


@pytest.mark.django_db
def test_index_page(api_client: APIClient):
    response = api_client.get("/")
    assert response.status_code == status.HTTP_200_OK
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    content = response.content.decode("utf-8")
    assert "Fuel Route Planner" in content
    assert "start-input" in content
    assert "finish-input" in content
    assert "leaflet.js" in content
    assert "app.js" in content


@pytest.mark.django_db
def test_referrer_policy_header(api_client: APIClient):
    response = api_client.get("/")
    assert response.status_code == status.HTTP_200_OK
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


@pytest.mark.django_db
def test_station_list_and_filter(api_client: APIClient):
    Station.objects.create(
        opis_id=3001,
        name="Pilot Station",
        address="10 Route 66",
        city="Flagstaff",
        state="AZ",
        rack_id=5,
        retail_price=Decimal("3.25000"),
        latitude=35.1983,
        longitude=-111.6513,
    )
    Station.objects.create(
        opis_id=3002,
        name="Texaco Travel Center",
        address="500 I-10",
        city="Houston",
        state="TX",
        rack_id=9,
        retail_price=Decimal("2.95000"),
        latitude=29.7604,
        longitude=-95.3698,
    )

    response = api_client.get("/api/v1/stations/?state=AZ")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Pilot Station"


@pytest.mark.django_db
def test_call_budget_and_caching(api_client: APIClient, monkeypatch):
    call_counts = {"get": 0, "post": 0}

    def mock_get(url, *args, **kwargs):
        call_counts["get"] += 1
        resp = MagicMock()
        resp.status_code = 200
        text = kwargs.get("params", {}).get("text", "")
        if "Chicago" in text:
            coords = [-87.6298, 41.8781]
        else:
            coords = [-96.7970, 32.7767]
        resp.json.return_value = {"features": [{"geometry": {"coordinates": coords}}]}
        return resp

    def mock_post(url, *args, **kwargs):
        call_counts["post"] += 1
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "routes": [
                {
                    "summary": {"distance": 400000.0, "duration": 15000.0},
                    "geometry": {
                        "coordinates": [
                            [-87.6298, 41.8781],
                            [-96.7970, 32.7767],
                        ]
                    },
                }
            ]
        }
        return resp

    monkeypatch.setattr(requests, "get", mock_get)
    monkeypatch.setattr(requests, "post", mock_post)

    # 1. Coordinates in: exactly 1 call (directions call, no geocode calls)
    cache.clear()
    call_counts["get"] = 0
    call_counts["post"] = 0
    payload_coords = {
        "start": {"latitude": 41.8781, "longitude": -87.6298},
        "finish": {"latitude": 32.7767, "longitude": -96.7970},
    }
    resp1 = api_client.post("/api/v1/route/plan/", data=payload_coords, format="json")
    assert resp1.status_code == status.HTTP_200_OK
    assert call_counts["get"] == 0
    assert call_counts["post"] == 1
    total_calls_coords = call_counts["get"] + call_counts["post"]
    assert total_calls_coords == 1

    # 2. Text in: exactly 3 calls (2 geocode calls + 1 directions call)
    cache.clear()
    call_counts["get"] = 0
    call_counts["post"] = 0
    payload_text = {
        "start": "Chicago, IL",
        "finish": "Dallas, TX",
    }
    resp2 = api_client.post("/api/v1/route/plan/", data=payload_text, format="json")
    assert resp2.status_code == status.HTTP_200_OK
    assert call_counts["get"] == 2
    assert call_counts["post"] == 1
    total_calls_text = call_counts["get"] + call_counts["post"]
    assert total_calls_text == 3

    # 3. Repeated request: exactly 0 calls (both geocode and route are cached)
    calls_before = call_counts["get"] + call_counts["post"]
    resp3 = api_client.post("/api/v1/route/plan/", data=payload_text, format="json")
    assert resp3.status_code == status.HTTP_200_OK
    calls_after = call_counts["get"] + call_counts["post"]
    assert calls_after - calls_before == 0


@pytest.mark.django_db
def test_route_plan_invalid_input(api_client: APIClient):
    payload = {"start": {"latitude": 999.0, "longitude": -74.0060}}
    response = api_client.post("/api/v1/route/plan/", data=payload, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert "error" in data
    assert data["code"] == "INVALID_INPUT"


@pytest.mark.django_db
def test_route_plan_outside_contiguous_us(api_client: APIClient):
    # Honolulu, HI coordinates (lat ~21.3)
    payload = {
        "start": {"latitude": 21.3069, "longitude": -157.8583},
        "end": {"latitude": 32.7767, "longitude": -96.7970},
    }
    response = api_client.post("/api/v1/route/plan/", data=payload, format="json")
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert "error" in data
    assert data["code"] == "OUTSIDE_CONTIGUOUS_US"


@pytest.mark.django_db
def test_route_plan_routing_unavailable(api_client: APIClient, monkeypatch):
    def mock_fail(*args, **kwargs):
        raise requests.Timeout("Gateway timeout")

    monkeypatch.setattr(requests, "post", mock_fail)

    payload = {
        "start": {"latitude": 41.8781, "longitude": -87.6298},
        "end": {"latitude": 32.7767, "longitude": -96.7970},
    }
    response = api_client.post("/api/v1/route/plan/", data=payload, format="json")
    assert response.status_code == status.HTTP_502_BAD_GATEWAY
    data = response.json()
    assert "error" in data
    assert data["code"] == "ROUTING_UNAVAILABLE"


@pytest.mark.django_db
def test_route_plan_no_route_found(api_client: APIClient, monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.json.return_value = {"error": "Could not find point"}
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: mock_resp)

    payload = {
        "start": {"latitude": 41.8781, "longitude": -87.6298},
        "end": {"latitude": 32.7767, "longitude": -96.7970},
    }
    response = api_client.post("/api/v1/route/plan/", data=payload, format="json")
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    data = response.json()
    assert "error" in data
    assert data["code"] == "NO_ROUTE_FOUND"


@pytest.mark.django_db
def test_route_plan_no_feasible_stops(api_client: APIClient, monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "routes": [
            {
                "summary": {"distance": 1500000.0, "duration": 50000.0},
                "geometry": {
                    "coordinates": [
                        [-87.6298, 41.8781],
                        [-96.7970, 32.7767],
                    ]
                },
            }
        ]
    }
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: mock_resp)

    payload = {
        "start": {"latitude": 41.8781, "longitude": -87.6298},
        "end": {"latitude": 32.7767, "longitude": -96.7970},
    }
    response = api_client.post("/api/v1/route/plan/", data=payload, format="json")
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    data = response.json()
    assert "error" in data
    assert data["code"] == "NO_FEASIBLE_STOPS"


@pytest.mark.django_db
def test_route_plan_response_fields(api_client: APIClient, monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "routes": [
            {
                "summary": {"distance": 1126540.8, "duration": 36000.0},
                "geometry": {
                    "coordinates": [
                        [-88.0, 40.0],
                        [-80.0, 40.0],
                        [-75.0, 40.0],
                    ]
                },
            }
        ]
    }
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: mock_resp)

    Station.objects.create(
        opis_id=8001,
        name="Turnpike Plaza",
        address="Mile 400",
        city="Bedford",
        state="PA",
        rack_id=1,
        retail_price=Decimal("3.25000"),
        latitude=40.0,
        longitude=-80.0,
    )

    payload = {
        "start": {"latitude": 40.0, "longitude": -88.0},
        "end": {"latitude": 40.0, "longitude": -75.0},
    }
    response = api_client.post("/api/v1/route/plan/", data=payload, format="json")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    assert "total_gallons_purchased" in data
    assert "initial_fuel_cost" in data
    assert "initial_fuel_price_basis" in data
    assert "assumptions" in data
    assert data["assumptions"]["tank_capacity_gallons"] == 50.0
    assert data["assumptions"]["mpg"] == 10.0
    assert data["assumptions"]["max_range_miles"] == 500.0
    assert data["assumptions"]["reserve_gallons"] == 0.0
    assert data["assumptions"]["corridor_radius_miles"] == 10.0
    assert data["assumptions"]["min_purchase_gallons"] == 0.0
    assert "initial_fuel_rule" in data["assumptions"]
    assert "initial_fuel_price_basis_type" in data["assumptions"]

    # Verify GeoJSON route
    assert "route" in data
    assert data["route"]["type"] == "FeatureCollection"
    features = data["route"]["features"]
    assert len(features) >= 3  # LineString + Start + Finish (+ stops)

    line_feature = next(f for f in features if f["geometry"]["type"] == "LineString")
    assert line_feature["properties"]["type"] == "route"
    # Ensure coordinates in [lng, lat] order
    first_coord = line_feature["geometry"]["coordinates"][0]
    assert len(first_coord) == 2
    assert -125.0 <= first_coord[0] <= -66.0  # longitude
    assert 24.0 <= first_coord[1] <= 50.0  # latitude

    point_features = [f for f in features if f["geometry"]["type"] == "Point"]
    for pf in point_features:
        props = pf["properties"]
        assert "type" in props
        assert "name" in props
        assert "price" in props
        assert "gallons" in props
        assert "cost" in props
        assert "mile" in props

    assert len(data["fuel_stops"]) >= 1
    stop = data["fuel_stops"][0]
    assert "latitude" in stop
    assert "longitude" in stop
    assert "gallons_purchased" in stop
    assert "price_per_gallon" in stop
    assert "cost" in stop
    assert "fuel_remaining_on_arrival" in stop
    assert stop["gallons_purchased"] > 0


@pytest.mark.django_db
def test_route_plan_with_min_purchase_gallons(api_client: APIClient, monkeypatch):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "routes": [
            {
                "summary": {"distance": 1046073.6, "duration": 36000.0},
                "geometry": {
                    "coordinates": [
                        [-88.0, 40.0],
                        [-75.0, 40.0],
                    ]
                },
            }
        ]
    }
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: mock_resp)

    st1 = Station.objects.create(
        opis_id=8101,
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
        opis_id=8102,
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

    payload = {
        "start": {"latitude": 40.0, "longitude": -88.0},
        "end": {"latitude": 40.0, "longitude": -75.0},
        "min_purchase_gallons": 8.0,
    }
    response = api_client.post("/api/v1/route/plan/", data=payload, format="json")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["assumptions"]["min_purchase_gallons"] == 8.0
    assert len(data["fuel_stops"]) == 1
    assert data["fuel_stops"][0]["station_id"] == st1.id


@pytest.mark.django_db
def test_route_plan_throttling(api_client: APIClient, monkeypatch):
    from apps.api.views import RoutePlanRateThrottle

    monkeypatch.setattr(RoutePlanRateThrottle, "rate", "2/minute")
    payload = {"start": "invalid_1", "end": "invalid_2"}
    r1 = api_client.post("/api/v1/route/plan/", data=payload, format="json")
    assert r1.status_code != status.HTTP_429_TOO_MANY_REQUESTS
    r2 = api_client.post("/api/v1/route/plan/", data=payload, format="json")
    assert r2.status_code != status.HTTP_429_TOO_MANY_REQUESTS
    r3 = api_client.post("/api/v1/route/plan/", data=payload, format="json")
    assert r3.status_code == status.HTTP_429_TOO_MANY_REQUESTS
