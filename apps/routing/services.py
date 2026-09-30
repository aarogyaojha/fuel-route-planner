import math
import re
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import numpy as np
import requests
import shapely
from django.conf import settings
from django.core.cache import cache
from shapely.geometry import LineString

from apps.stations.models import Station

EARTH_RADIUS_MILES = 3958.8

CONTIGUOUS_US_BOUNDS = {
    "min_lat": 24.52,
    "max_lat": 49.38,
    "min_lon": -124.78,
    "max_lon": -66.95,
}


class RoutingServiceError(Exception):
    def __init__(self, message: str, code: str = "ROUTING_ERROR"):
        super().__init__(message)
        self.message = message
        self.code = code


class InvalidLocationError(RoutingServiceError):
    def __init__(self, message: str, code: str = "INVALID_LOCATION"):
        super().__init__(message, code=code)


class RoutingUnavailable(RoutingServiceError):
    def __init__(
        self,
        message: str = "Routing provider is unreachable or timed out.",
        code: str = "ROUTING_UNAVAILABLE",
    ):
        super().__init__(message, code=code)


class NoRouteFound(RoutingServiceError):
    def __init__(
        self,
        message: str = "No route found between the specified locations.",
        code: str = "NO_ROUTE_FOUND",
    ):
        super().__init__(message, code=code)


class NoFeasibleStopsError(RoutingServiceError):
    def __init__(
        self,
        message: str = "No feasible chain of fuel stops exists along the route.",
        code: str = "NO_FEASIBLE_STOPS",
    ):
        super().__init__(message, code=code)


@dataclass(frozen=True)
class Coordinate:
    latitude: float
    longitude: float


@dataclass
class FuelStop:
    station_id: int
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    latitude: float
    longitude: float
    retail_price: Decimal
    distance_along_route_miles: float
    gallons_refueled: float
    gallons_purchased: float
    price_per_gallon: Decimal
    fuel_remaining_on_arrival: float
    cost: Decimal


@dataclass
class RoutePlanResult:
    total_distance_miles: float
    total_duration_hours: float
    total_fuel_cost: Decimal
    total_gallons_consumed: float
    total_gallons_purchased: float
    initial_fuel_cost: Decimal
    initial_fuel_price_basis: Decimal
    assumptions: dict[str, Any]
    fuel_stops: list[FuelStop]
    route: dict[str, Any]
    start: dict[str, float]
    end: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["total_fuel_cost"] = str(self.total_fuel_cost)
        result["initial_fuel_cost"] = str(self.initial_fuel_cost)
        result["initial_fuel_price_basis"] = str(self.initial_fuel_price_basis)
        for stop in result["fuel_stops"]:
            stop["retail_price"] = str(stop["retail_price"])
            stop["price_per_gallon"] = str(stop["price_per_gallon"])
            stop["cost"] = str(stop["cost"])
        return result


def is_in_contiguous_us(lat: float, lon: float) -> bool:
    """Check whether coordinates fall within the contiguous United States."""
    return (
        CONTIGUOUS_US_BOUNDS["min_lat"] <= lat <= CONTIGUOUS_US_BOUNDS["max_lat"]
        and CONTIGUOUS_US_BOUNDS["min_lon"] <= lon <= CONTIGUOUS_US_BOUNDS["max_lon"]
    )


def geocode_location(query: str) -> Coordinate:
    """Geocode a location query string using ORS, with contiguous US validation and caching."""
    normalized = " ".join(query.strip().lower().split())
    if not normalized:
        raise InvalidLocationError("Location string cannot be empty.", code="INVALID_LOCATION")

    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", normalized)
    cache_key = f"geocode:{safe_name}"
    cached = cache.get(cache_key)
    if cached is not None:
        lat, lon = cached
        if not is_in_contiguous_us(lat, lon):
            raise InvalidLocationError(
                f"Location '{query}' is outside the contiguous United States.",
                code="OUTSIDE_CONTIGUOUS_US",
            )
        return Coordinate(latitude=lat, longitude=lon)

    ors_api_key = getattr(settings, "ORS_API_KEY", "")
    url = "https://api.openrouteservice.org/geocode/search"
    params = {"text": query, "boundary.country": "US", "size": 1}
    headers = {"Authorization": ors_api_key}

    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
    except requests.Timeout as exc:
        raise RoutingUnavailable(
            "Geocoding service timed out.", code="ROUTING_UNAVAILABLE"
        ) from exc
    except requests.RequestException as exc:
        raise RoutingUnavailable(
            "Geocoding service is unavailable.", code="ROUTING_UNAVAILABLE"
        ) from exc

    if response.status_code >= 500 or response.status_code == 429:
        raise RoutingUnavailable(
            "Geocoding service returned an upstream error.", code="ROUTING_UNAVAILABLE"
        )
    if response.status_code != 200:
        raise RoutingUnavailable("Geocoding request failed.", code="ROUTING_UNAVAILABLE")

    data = response.json()
    features = data.get("features", [])
    if not features:
        raise InvalidLocationError(
            f"Unable to geocode location: '{query}'.", code="INVALID_LOCATION"
        )

    coords = features[0]["geometry"]["coordinates"]
    lon, lat = float(coords[0]), float(coords[1])

    if not is_in_contiguous_us(lat, lon):
        raise InvalidLocationError(
            f"Location '{query}' is outside the contiguous United States.",
            code="OUTSIDE_CONTIGUOUS_US",
        )

    cache.set(cache_key, (lat, lon), timeout=30 * 86400)
    return Coordinate(latitude=lat, longitude=lon)


def resolve_location(location_input: Coordinate | dict[str, Any] | str) -> Coordinate:
    """Resolve location input (Coordinate instance, coordinate dict, or text) to Coordinate."""
    if isinstance(location_input, Coordinate):
        if not is_in_contiguous_us(location_input.latitude, location_input.longitude):
            raise InvalidLocationError(
                f"Coordinates ({location_input.latitude}, {location_input.longitude}) are outside the contiguous United States.",
                code="OUTSIDE_CONTIGUOUS_US",
            )
        return location_input

    if isinstance(location_input, str):
        return geocode_location(location_input)

    if isinstance(location_input, dict):
        try:
            lat = float(location_input["latitude"])
            lon = float(location_input["longitude"])
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidLocationError(
                "Coordinates must contain numeric latitude and longitude.",
                code="INVALID_INPUT",
            ) from exc

        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise InvalidLocationError(
                "Latitude must be between -90 and 90, and longitude between -180 and 180.",
                code="INVALID_INPUT",
            )
        if not is_in_contiguous_us(lat, lon):
            raise InvalidLocationError(
                f"Coordinates ({lat}, {lon}) are outside the contiguous United States.",
                code="OUTSIDE_CONTIGUOUS_US",
            )
        return Coordinate(latitude=lat, longitude=lon)

    raise InvalidLocationError(
        "Location must be a coordinate dictionary or string.",
        code="INVALID_INPUT",
    )


def haversine_distance_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two geographic points in miles."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_MILES * c


def decode_polyline(encoded: str, precision: int = 5) -> list[list[float]]:
    """Decode an encoded polyline string into list of [latitude, longitude]."""
    points: list[list[float]] = []
    index = 0
    lat = 0
    lng = 0
    factor = 10**precision

    while index < len(encoded):
        shift = 0
        result = 0
        while True:
            byte = ord(encoded[index]) - 63
            index += 1
            result |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        dlat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += dlat

        shift = 0
        result = 0
        while True:
            byte = ord(encoded[index]) - 63
            index += 1
            result |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        dlng = ~(result >> 1) if (result & 1) else (result >> 1)
        lng += dlng

        points.append([round(lat / factor, 6), round(lng / factor, 6)])

    return points


def fetch_route(
    start: Coordinate,
    end: Coordinate,
) -> dict[str, Any]:
    """Fetch routing geometry, total distance, and duration between two coordinates via ORS."""
    cache_key = f"route:{round(start.latitude, 4)}_{round(start.longitude, 4)}_{round(end.latitude, 4)}_{round(end.longitude, 4)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    ors_api_key = getattr(settings, "ORS_API_KEY", "")
    url = "https://api.openrouteservice.org/v2/directions/driving-car"
    headers = {"Authorization": ors_api_key, "Content-Type": "application/json"}
    payload = {"coordinates": [[start.longitude, start.latitude], [end.longitude, end.latitude]]}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
    except requests.Timeout as exc:
        raise RoutingUnavailable("Routing service timed out.", code="ROUTING_UNAVAILABLE") from exc
    except requests.RequestException as exc:
        raise RoutingUnavailable(
            "Routing service is unavailable.", code="ROUTING_UNAVAILABLE"
        ) from exc

    if response.status_code in (404, 422):
        raise NoRouteFound("No route found between the specified locations.", code="NO_ROUTE_FOUND")
    if response.status_code >= 500 or response.status_code == 429:
        raise RoutingUnavailable(
            "Routing service returned an upstream error.", code="ROUTING_UNAVAILABLE"
        )
    if response.status_code != 200:
        try:
            err_json = response.json()
            err_msg = str(err_json.get("error", ""))
            if any(
                term in err_msg.lower() for term in ["could not find", "no route", "not routable"]
            ):
                raise NoRouteFound(f"No route found: {err_msg}", code="NO_ROUTE_FOUND")
        except (ValueError, TypeError):
            pass
        raise RoutingUnavailable("Routing request failed.", code="ROUTING_UNAVAILABLE")

    data = response.json()
    routes = data.get("routes", [])
    if not routes:
        raise NoRouteFound("No route found between the specified locations.", code="NO_ROUTE_FOUND")

    route = routes[0]
    summary = route["summary"]
    distance_miles = summary["distance"] * 0.000621371
    duration_hours = summary["duration"] / 3600.0

    geometry = route.get("geometry")
    if isinstance(geometry, str):
        coords = decode_polyline(geometry)
    elif isinstance(geometry, dict) and "coordinates" in geometry:
        coords = [[pt[1], pt[0]] for pt in geometry["coordinates"]]
    else:
        coords = [[start.latitude, start.longitude], [end.latitude, end.longitude]]

    result = {
        "distance_miles": round(distance_miles, 2),
        "duration_hours": round(duration_hours, 2),
        "coordinates": coords,
    }
    cache.set(cache_key, result, timeout=3600)
    return result


def find_candidate_stations(
    route_coordinates: list[list[float]],
    corridor_radius_miles: float,
    total_distance_miles: float | None = None,
) -> list[dict[str, Any]]:
    """Find stations located within the corridor radius along the route polyline."""
    if not route_coordinates:
        return []

    coords_arr = np.array(route_coordinates, dtype=np.float64)
    lats = coords_arr[:, 0]
    lons = coords_arr[:, 1]
    min_lat, max_lat = float(np.min(lats)), float(np.max(lats))
    min_lon, max_lon = float(np.min(lons)), float(np.max(lons))

    deg_pad = (corridor_radius_miles / 69.0) + 0.1
    candidates = list(
        Station.objects.filter(
            latitude__isnull=False,
            longitude__isnull=False,
            latitude__gte=min_lat - deg_pad,
            latitude__lte=max_lat + deg_pad,
            longitude__gte=min_lon - deg_pad,
            longitude__lte=max_lon + deg_pad,
        )
    )
    if not candidates:
        return []

    mean_lat = (min_lat + max_lat) / 2.0
    kx = 69.0 * math.cos(math.radians(mean_lat))
    ky = 69.0

    route_xy = np.column_stack([lons * kx, lats * ky])
    route_line = LineString(route_xy)
    simplified_line = route_line.simplify(0.1, preserve_topology=False)

    st_lats = np.array([float(s.latitude) for s in candidates], dtype=np.float64)
    st_lons = np.array([float(s.longitude) for s in candidates], dtype=np.float64)
    st_pts = shapely.points(st_lons * kx, st_lats * ky)

    dists = shapely.distance(simplified_line, st_pts)
    mask = dists <= corridor_radius_miles
    matching_indices = np.where(mask)[0]
    if len(matching_indices) == 0:
        return []

    line_len = simplified_line.length
    scale = (total_distance_miles / line_len) if (total_distance_miles and line_len > 0) else 1.0
    matched_pts = st_pts[matching_indices]
    locs = shapely.line_locate_point(simplified_line, matched_pts)

    matched_stations: list[dict[str, Any]] = []
    for idx, loc, d in zip(matching_indices, locs, dists[matching_indices], strict=False):
        matched_stations.append(
            {
                "station": candidates[idx],
                "route_mile": round(float(loc * scale), 2),
                "corridor_dist": round(float(d), 2),
            }
        )

    matched_stations.sort(key=lambda item: item["route_mile"])
    return matched_stations


def build_route_geojson(
    route_coordinates: list[list[float]],
    start: dict[str, float],
    end: dict[str, float],
    fuel_stops: list[FuelStop],
    total_distance_miles: float,
    max_points: int = 1500,
) -> dict[str, Any]:
    """Build GeoJSON FeatureCollection with simplified route LineString and Point features."""
    lng_lat_coords = [[pt[1], pt[0]] for pt in route_coordinates]
    if len(lng_lat_coords) <= max_points:
        simplified_coords = lng_lat_coords
    else:
        line = LineString(lng_lat_coords)
        tol = 0.002
        simplified = line.simplify(tol, preserve_topology=False)
        while len(simplified.coords) > max_points:
            tol *= 1.5
            simplified = line.simplify(tol, preserve_topology=False)
        simplified_coords = [[round(c[0], 6), round(c[1], 6)] for c in simplified.coords]
        simplified_coords[0] = lng_lat_coords[0]
        simplified_coords[-1] = lng_lat_coords[-1]

    features: list[dict[str, Any]] = [
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": simplified_coords,
            },
            "properties": {"type": "route"},
        },
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [start["longitude"], start["latitude"]],
            },
            "properties": {
                "type": "start",
                "name": "Start",
                "price": None,
                "gallons": None,
                "cost": None,
                "mile": 0.0,
            },
        },
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [end["longitude"], end["latitude"]],
            },
            "properties": {
                "type": "finish",
                "name": "Finish",
                "price": None,
                "gallons": None,
                "cost": None,
                "mile": total_distance_miles,
            },
        },
    ]

    for stop in fuel_stops:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [stop.longitude, stop.latitude],
                },
                "properties": {
                    "type": "fuel_stop",
                    "name": stop.name,
                    "price": str(stop.price_per_gallon),
                    "gallons": stop.gallons_purchased,
                    "cost": str(stop.cost),
                    "mile": stop.distance_along_route_miles,
                },
            }
        )

    return {"type": "FeatureCollection", "features": features}


def _run_optimizer_simulation(
    candidates: list[dict[str, Any]],
    total_distance: float,
    range_miles: float,
    reserve_miles: float,
    tank_cap: float,
    vehicle_mpg: float,
    reserve_gal: float,
) -> tuple[list[FuelStop], Decimal, float]:
    can_reach_dest = [False] * len(candidates)
    for i in range(len(candidates) - 1, -1, -1):
        dist_to_dest = total_distance - candidates[i]["route_mile"]
        if dist_to_dest <= range_miles - reserve_miles:
            can_reach_dest[i] = True
        else:
            for j in range(i + 1, len(candidates)):
                if candidates[j]["route_mile"] - candidates[i]["route_mile"] > range_miles:
                    break
                if can_reach_dest[j]:
                    can_reach_dest[i] = True
                    break

    feasible = [c for i, c in enumerate(candidates) if can_reach_dest[i]]
    start_reachable = [c for c in feasible if c["route_mile"] <= range_miles]
    if not start_reachable:
        raise NoFeasibleStopsError(
            "No feasible chain of fuel stops exists along the route.",
            code="NO_FEASIBLE_STOPS",
        )

    first_stop = min(start_reachable, key=lambda s: (s["station"].retail_price, -s["route_mile"]))
    curr_station = first_stop
    fuel_on_arrival = tank_cap - (curr_station["route_mile"] / vehicle_mpg)

    selected_stops: list[FuelStop] = []
    stops_fuel_cost = Decimal("0.00")
    total_gallons_purchased = 0.0

    while True:
        curr_mile = curr_station["route_mile"]
        curr_price = curr_station["station"].retail_price

        reachable_ahead = [
            c for c in feasible if curr_mile < c["route_mile"] <= curr_mile + range_miles
        ]
        cheaper_ahead = [c for c in reachable_ahead if c["station"].retail_price < curr_price]
        dist_to_dest = total_distance - curr_mile
        dest_in_range = dist_to_dest <= range_miles - reserve_miles

        if cheaper_ahead:
            next_station = cheaper_ahead[0]
            dist_to_next = next_station["route_mile"] - curr_mile
            fuel_needed = dist_to_next / vehicle_mpg
            gallons_to_buy = max(0.0, fuel_needed - fuel_on_arrival)
            gallons_rounded = round(gallons_to_buy, 2)
            cost = (Decimal(str(gallons_rounded)) * curr_price).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            if gallons_rounded > 0:
                st = curr_station["station"]
                selected_stops.append(
                    FuelStop(
                        station_id=st.id,
                        opis_id=st.opis_id,
                        name=st.name,
                        address=st.address,
                        city=st.city,
                        state=st.state,
                        latitude=st.latitude,
                        longitude=st.longitude,
                        retail_price=curr_price,
                        distance_along_route_miles=round(curr_mile, 2),
                        gallons_refueled=gallons_rounded,
                        gallons_purchased=gallons_rounded,
                        price_per_gallon=curr_price,
                        fuel_remaining_on_arrival=round(fuel_on_arrival, 2),
                        cost=cost,
                    )
                )
                stops_fuel_cost += cost
                total_gallons_purchased += gallons_rounded

            fuel_on_arrival = max(0.0, fuel_on_arrival + gallons_to_buy - fuel_needed)
            curr_station = next_station

        elif dest_in_range:
            fuel_needed = (dist_to_dest / vehicle_mpg) + reserve_gal
            gallons_to_buy = max(0.0, fuel_needed - fuel_on_arrival)
            gallons_rounded = round(gallons_to_buy, 2)
            cost = (Decimal(str(gallons_rounded)) * curr_price).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            if gallons_rounded > 0:
                st = curr_station["station"]
                selected_stops.append(
                    FuelStop(
                        station_id=st.id,
                        opis_id=st.opis_id,
                        name=st.name,
                        address=st.address,
                        city=st.city,
                        state=st.state,
                        latitude=st.latitude,
                        longitude=st.longitude,
                        retail_price=curr_price,
                        distance_along_route_miles=round(curr_mile, 2),
                        gallons_refueled=gallons_rounded,
                        gallons_purchased=gallons_rounded,
                        price_per_gallon=curr_price,
                        fuel_remaining_on_arrival=round(fuel_on_arrival, 2),
                        cost=cost,
                    )
                )
                stops_fuel_cost += cost
                total_gallons_purchased += gallons_rounded
            break

        else:
            gallons_to_buy = max(0.0, tank_cap - fuel_on_arrival)
            gallons_rounded = round(gallons_to_buy, 2)
            cost = (Decimal(str(gallons_rounded)) * curr_price).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            if gallons_rounded > 0:
                st = curr_station["station"]
                selected_stops.append(
                    FuelStop(
                        station_id=st.id,
                        opis_id=st.opis_id,
                        name=st.name,
                        address=st.address,
                        city=st.city,
                        state=st.state,
                        latitude=st.latitude,
                        longitude=st.longitude,
                        retail_price=curr_price,
                        distance_along_route_miles=round(curr_mile, 2),
                        gallons_refueled=gallons_rounded,
                        gallons_purchased=gallons_rounded,
                        price_per_gallon=curr_price,
                        fuel_remaining_on_arrival=round(fuel_on_arrival, 2),
                        cost=cost,
                    )
                )
                stops_fuel_cost += cost
                total_gallons_purchased += gallons_rounded

            if not reachable_ahead:
                raise NoFeasibleStopsError(
                    "No feasible chain of fuel stops exists along the route.",
                    code="NO_FEASIBLE_STOPS",
                )

            next_station = min(
                reachable_ahead, key=lambda s: (s["station"].retail_price, -s["route_mile"])
            )
            dist_to_next = next_station["route_mile"] - curr_mile
            fuel_on_arrival = tank_cap - (dist_to_next / vehicle_mpg)
            curr_station = next_station

    return selected_stops, stops_fuel_cost, total_gallons_purchased


def plan_optimal_fuel_route(
    start: Coordinate | dict[str, Any] | str,
    end: Coordinate | dict[str, Any] | str,
    max_range_miles: float | None = None,
    mpg: float | None = None,
    corridor_radius_miles: float | None = None,
    tank_capacity_gallons: float | None = None,
    reserve_gallons: float | None = None,
    min_purchase_gallons: float | None = None,
) -> RoutePlanResult:
    """Plan route and determine optimal fuel stops to minimize overall fuel expense."""
    start_coord = resolve_location(start)
    end_coord = resolve_location(end)

    tank_cap = tank_capacity_gallons or getattr(settings, "VEHICLE_TANK_CAPACITY_GALLONS", 50.0)
    vehicle_mpg = mpg or getattr(settings, "VEHICLE_MPG", 10.0)
    range_miles = max_range_miles or (tank_cap * vehicle_mpg)
    reserve_gal = (
        reserve_gallons
        if reserve_gallons is not None
        else getattr(settings, "VEHICLE_RESERVE_GALLONS", 0.0)
    )
    corridor_radius = corridor_radius_miles or getattr(settings, "CORRIDOR_RADIUS_MILES", 10.0)
    initial_fuel_radius = getattr(settings, "INITIAL_FUEL_RADIUS_MILES", 50.0)
    min_purchase = (
        min_purchase_gallons
        if min_purchase_gallons is not None
        else getattr(settings, "MIN_PURCHASE_GALLONS", 0.0)
    )
    reserve_miles = reserve_gal * vehicle_mpg

    route_data = fetch_route(start_coord, end_coord)
    total_distance = route_data["distance_miles"]
    duration_hours = route_data["duration_hours"]
    coordinates = route_data["coordinates"]

    total_gallons_consumed = round(total_distance / vehicle_mpg, 2)

    candidates = find_candidate_stations(coordinates, corridor_radius, total_distance)
    valid_candidates = [c for c in candidates if 0 < c["route_mile"] < total_distance]

    near_start = [c for c in valid_candidates if c["route_mile"] <= initial_fuel_radius]
    if near_start:
        initial_fuel_price_basis = min(c["station"].retail_price for c in near_start)
        basis_type = "cheapest_near_start"
    elif valid_candidates:
        avg_price = sum(c["station"].retail_price for c in valid_candidates) / len(valid_candidates)
        initial_fuel_price_basis = avg_price.quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
        basis_type = "corridor_average"
    else:
        initial_fuel_price_basis = Decimal("0.00000")
        basis_type = "none"

    assumptions = {
        "tank_capacity_gallons": tank_cap,
        "mpg": vehicle_mpg,
        "max_range_miles": range_miles,
        "reserve_gallons": reserve_gal,
        "corridor_radius_miles": corridor_radius,
        "min_purchase_gallons": min_purchase,
        "initial_fuel_rule": (
            "Fuel used from start to the first stop (or destination if no stops) is priced "
            "at the cheapest corridor station near the start (<= 50 miles), "
            "or the average price of corridor stations if none is near the start."
        ),
        "initial_fuel_price_basis_type": basis_type,
    }

    start_dict = {"latitude": start_coord.latitude, "longitude": start_coord.longitude}
    end_dict = {"latitude": end_coord.latitude, "longitude": end_coord.longitude}

    if total_distance <= range_miles - reserve_miles:
        initial_fuel_gallons = round(total_distance / vehicle_mpg, 2)
        initial_fuel_cost = (
            Decimal(str(initial_fuel_gallons)) * initial_fuel_price_basis
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        route_geojson = build_route_geojson(
            coordinates,
            start=start_dict,
            end=end_dict,
            fuel_stops=[],
            total_distance_miles=total_distance,
        )
        return RoutePlanResult(
            total_distance_miles=total_distance,
            total_duration_hours=duration_hours,
            total_fuel_cost=initial_fuel_cost,
            total_gallons_consumed=total_gallons_consumed,
            total_gallons_purchased=0.0,
            initial_fuel_cost=initial_fuel_cost,
            initial_fuel_price_basis=initial_fuel_price_basis,
            assumptions=assumptions,
            fuel_stops=[],
            route=route_geojson,
            start=start_dict,
            end=end_dict,
        )

    current_candidates = list(valid_candidates)
    selected_stops, stops_fuel_cost, total_gallons_purchased = _run_optimizer_simulation(
        current_candidates,
        total_distance,
        range_miles,
        reserve_miles,
        tank_cap,
        vehicle_mpg,
        reserve_gal,
    )

    if min_purchase > 0:
        cannot_skip_ids: set[int] = set()
        while True:
            under_stops = [
                s
                for s in selected_stops
                if 0 < s.gallons_purchased < min_purchase and s.station_id not in cannot_skip_ids
            ]
            if not under_stops:
                break

            under_stops.sort(key=lambda s: s.gallons_purchased)
            stop_to_skip = under_stops[0]

            test_candidates = [
                c for c in current_candidates if c["station"].id != stop_to_skip.station_id
            ]
            try:
                new_stops, new_cost, new_purchased = _run_optimizer_simulation(
                    test_candidates,
                    total_distance,
                    range_miles,
                    reserve_miles,
                    tank_cap,
                    vehicle_mpg,
                    reserve_gal,
                )
                current_candidates = test_candidates
                selected_stops = new_stops
                stops_fuel_cost = new_cost
                total_gallons_purchased = new_purchased
            except NoFeasibleStopsError:
                cannot_skip_ids.add(stop_to_skip.station_id)

    if selected_stops:
        initial_leg_miles = selected_stops[0].distance_along_route_miles
        initial_fuel_gallons = round(initial_leg_miles / vehicle_mpg, 2)
        initial_fuel_cost = (
            Decimal(str(initial_fuel_gallons)) * initial_fuel_price_basis
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_fuel_cost = (stops_fuel_cost + initial_fuel_cost).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    else:
        initial_fuel_gallons = round(total_distance / vehicle_mpg, 2)
        initial_fuel_cost = (
            Decimal(str(initial_fuel_gallons)) * initial_fuel_price_basis
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_fuel_cost = initial_fuel_cost

    route_geojson = build_route_geojson(
        coordinates,
        start=start_dict,
        end=end_dict,
        fuel_stops=selected_stops,
        total_distance_miles=total_distance,
    )

    return RoutePlanResult(
        total_distance_miles=total_distance,
        total_duration_hours=duration_hours,
        total_fuel_cost=total_fuel_cost,
        total_gallons_consumed=total_gallons_consumed,
        total_gallons_purchased=round(total_gallons_purchased, 2),
        initial_fuel_cost=initial_fuel_cost,
        initial_fuel_price_basis=initial_fuel_price_basis,
        assumptions=assumptions,
        fuel_stops=selected_stops,
        route=route_geojson,
        start=start_dict,
        end=end_dict,
    )
