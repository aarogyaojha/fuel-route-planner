# Fuel Route Planner

A Django REST Framework service that computes cost-optimal refueling stops along driving routes within the contiguous United States. Given a start and destination, it retrieves the road route from OpenRouteService, filters candidate fuel stations within a 10-mile corridor using OPIS retail price data, and computes the cheapest refueling plan for a vehicle with a 500-mile range.

## Setup

### Prerequisites

- Python 3.12
- Make

### Environment Configuration

Copy the example environment file and set your OpenRouteService API key:

```bash
cp .env.example .env
```

Edit `.env`:

```env
DJANGO_SECRET_KEY=change-me-in-production
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,[::1]
DJANGO_SETTINGS_MODULE=config.settings.local
DATABASE_URL=sqlite:///db.sqlite3
ORS_API_KEY=your_openrouteservice_api_key_here
```

### Quickstart with Makefile

```bash
make install
make migrate
make load-data
make run
```

- `make install`: Installs dependencies from `requirements.txt`.
- `make migrate`: Applies database migrations.
- `make load-data`: Imports station records from `data/fuel-prices-for-be-assessment.csv` (8,151 rows read, drops non-US and duplicates, saves 6,626 stations) and geocodes coordinates offline via GeoNames US dataset.
- `make run`: Starts the local development server at `http://127.0.0.1:8000/`.
- `make test`: Executes test suite (`pytest`).
- `make lint`: Checks linting and formatting (`ruff check . && ruff format --check .`).

### Production Server

Run with Gunicorn using production settings:

```bash
DJANGO_SETTINGS_MODULE=config.settings.production gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 4
```

## API Endpoint

### Plan Route

`POST /api/v1/route/plan/`

#### Input Formats

The endpoint accepts `start` and `end` as either city/address strings or coordinate objects:

- City or address string: `"New York, NY"`, `"Chicago, IL"`, `"1600 Pennsylvania Ave NW, Washington, DC"`
- Coordinate object: `{"latitude": 41.8781, "longitude": -87.6298}`

Optional tuning parameters:

- `max_range_miles` (float, default `500.0`): Vehicle maximum range on a full tank.
- `mpg` (float, default `10.0`): Vehicle miles per gallon.
- `corridor_radius_miles` (float, default `10.0`): Corridor buffer radius around route polyline.

#### Example Request

```json
{
  "start": "Chicago, IL",
  "end": "Dallas, TX"
}
```

#### Example Response

```json
{
  "total_distance_miles": 925.3,
  "total_duration_hours": 14.8,
  "total_fuel_cost": "278.45",
  "total_gallons_consumed": 92.53,
  "initial_fuel_cost": "150.00",
  "initial_fuel_price_basis": "3.00000",
  "fuel_stops": [
    {
      "station_id": 1421,
      "opis_id": 104231,
      "name": "Pilot Travel Center #241",
      "address": "1200 E Highway 60",
      "city": "Mount Vernon",
      "state": "MO",
      "latitude": 37.1039,
      "longitude": -93.8185,
      "retail_price": "2.89900",
      "distance_along_route_miles": 482.1,
      "gallons_purchased": 44.32,
      "price_per_gallon": "2.89900",
      "cost": "128.45",
      "fuel_remaining_on_arrival": 1.79
    }
  ],
  "start": {
    "latitude": 41.8781,
    "longitude": -87.6298
  },
  "end": {
    "latitude": 32.7767,
    "longitude": -96.7970
  },
  "assumptions": {
    "mpg": 10.0,
    "tank_capacity_gallons": 50.0,
    "max_range_miles": 500.0,
    "reserve_gallons": 0.0,
    "corridor_radius_miles": 10.0,
    "initial_fuel_rule": "Initial tank consumed up to first stop (or destination) is priced at cheapest station near start (50mi) or corridor average",
    "initial_fuel_price_basis_type": "cheapest_near_start"
  },
  "route": {
    "type": "FeatureCollection",
    "features": [
      {
        "type": "Feature",
        "geometry": {
          "type": "LineString",
          "coordinates": [
            [-87.6298, 41.8781],
            [-87.7142, 41.8321],
            [-96.7970, 32.7767]
          ]
        },
        "properties": {
          "type": "route",
          "name": "Directions route"
        }
      },
      {
        "type": "Feature",
        "geometry": {
          "type": "Point",
          "coordinates": [-87.6298, 41.8781]
        },
        "properties": {
          "type": "start",
          "name": "Start Location",
          "price": null,
          "gallons": null,
          "cost": null,
          "mile": 0.0
        }
      },
      {
        "type": "Feature",
        "geometry": {
          "type": "Point",
          "coordinates": [-93.8185, 37.1039]
        },
        "properties": {
          "type": "fuel_stop",
          "name": "Pilot Travel Center #241",
          "price": "2.89900",
          "gallons": 44.32,
          "cost": "128.45",
          "mile": 482.1
        }
      },
      {
        "type": "Feature",
        "geometry": {
          "type": "Point",
          "coordinates": [-96.7970, 32.7767]
        },
        "properties": {
          "type": "finish",
          "name": "Destination",
          "price": null,
          "gallons": null,
          "cost": null,
          "mile": 925.3
        }
      }
    ]
  }
}
```

## Error Codes

| Status Code | Error Code | Description |
|:---|:---|:---|
| 400 | `INVALID_INPUT` | Request body failed JSON schema validation or contained invalid parameter types. |
| 400 | `INVALID_LOCATION` | Location query string could not be resolved by the geocoding provider. |
| 400 | `OUTSIDE_CONTIGUOUS_US` | Start or destination coordinates fall outside the contiguous 48 US states and DC. |
| 422 | `NO_ROUTE_FOUND` | OpenRouteService returned no drivable route between the provided locations. |
| 422 | `NO_FEASIBLE_STOPS` | Distance between reachable stations exceeds the vehicle maximum range (500 miles). |
| 429 | `THROTTLED` | Client IP exceeded rate limit of 60 requests per minute on the route plan endpoint. |
| 502 | `ROUTING_UNAVAILABLE` | OpenRouteService API is unreachable, timed out, or returned an upstream error. |

## Assumptions

- **Vehicle Range and Economy**: 10.0 MPG fuel economy and a 500.0-mile maximum driving range, corresponding to a 50.0-gallon tank capacity.
- **Starting Tank**: The vehicle departs with a full 50.0-gallon tank.
- **Initial Fuel Pricing**: To accurately reflect trip cost (especially for short trips under 500 miles requiring zero refueling stops), the fuel consumed from the start to the first stop—or to the destination if no stops are required—is priced using the cheapest fuel station within 50 miles of the departure point along the corridor. If no station exists near the start, the average price of all corridor stations is applied.
- **Station Coordinates**: The raw OPIS dataset provides city and state names without coordinates. Station locations are mapped offline to their city centroid using GeoNames populated places (feature class P), selecting the highest-population match for duplicate city names.
- **Search Corridor**: Candidate stations are filtered by a 10.0-mile buffer around the route polyline.

## Optimizer

Candidate stations inside the corridor are projected onto the route to determine their cumulative driving distance from the departure point. The algorithm employs a greedy lookahead strategy: from the current position with current fuel remaining, it evaluates all stations reachable within the maximum range. If a reachable station offers a cheaper price per gallon than the current station, the vehicle purchases only enough fuel to reach that cheaper station. If no reachable station is cheaper, the vehicle fills to maximum capacity (or purchases only the fuel necessary to reach the destination if within range). The trip completes with zero excess fuel beyond the configurable reserve.

## External Call Budget

The service operates under a strict external API budget:
- At most 1 OpenRouteService directions API call per uncached route.
- At most 2 OpenRouteService geocoding API calls (one per string location).
- Maximum of 3 external calls total per route request.

Geocoding responses are cached in memory for 30 days keyed on normalized query strings. Decoded route geometry and total distance are cached for 1 hour keyed on rounded start and end coordinates. Subsequent requests for the same route require 0 external calls.

Throttling is enforced at 60 requests per minute per client IP using Django REST Framework throttling.

## Measured Timings & Response Sizes

Measured on a local development instance with warm SQLite database:

| Route | Distance | Initial (Uncached) | Cached | Response Size |
|:---|:---|:---|:---|:---|
| New York, NY to Los Angeles, CA | 2,790 mi | 4,848 ms | 106 ms | 33.6 KB |
| Chicago, IL to Dallas, TX | 925 mi | 1,684 ms | 19 ms | 9.6 KB |
| New York, NY to Philadelphia, PA | 97 mi | 2,820 ms | 5 ms | 27.7 KB |

*Response size note*: Removing redundant raw `route_geometry` coordinates and retaining only the Douglas-Peucker simplified GeoJSON `route` reduced the New York to Los Angeles response payload from 527.6 KB to 33.6 KB (a 93.6% reduction).
