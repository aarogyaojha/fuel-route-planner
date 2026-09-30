import csv
import re
import string
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from io import TextIOWrapper
from pathlib import Path
from typing import IO

import requests
from django.conf import settings
from django.db import transaction

from apps.stations.models import Station

US_STATES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
}

GEONAMES_US_ZIP_URL = "https://download.geonames.org/export/dump/US.zip"


@dataclass
class ImportStats:
    rows_read: int
    non_us_dropped: int
    duplicates_dropped: int
    rows_saved: int


@dataclass
class GeocodeStats:
    matched: int
    unmatched: int
    unmatched_pairs: list[tuple[str, str]]


def normalize_city(name: str) -> str:
    """Normalize a city name by lowercasing, expanding abbreviations, and stripping punctuation."""
    if not name:
        return ""
    text = name.lower().strip()
    text = re.sub(r"\bst\.(?=\w)", "saint ", text)
    text = re.sub(r"\bft\.(?=\w)", "fort ", text)
    text = re.sub(r"\bmt\.(?=\w)", "mount ", text)
    text = re.sub(r"\bst\.", "saint", text)
    text = re.sub(r"\bft\.", "fort", text)
    text = re.sub(r"\bmt\.", "mount", text)
    text = re.sub(r"\bst\b", "saint", text)
    text = re.sub(r"\bft\b", "fort", text)
    text = re.sub(r"\bmt\b", "mount", text)
    text = text.replace("-", " ").replace("/", " ")
    text = text.translate(str.maketrans("", "", string.punctuation))
    return " ".join(text.split())


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace(" ", "_").replace("-", "_")


def import_stations_from_csv(
    file_source: str | Path | IO[str],
    batch_size: int = 1000,
) -> ImportStats:
    """Import fuel stations from CSV, filter US states, deduplicate on opis_id, and bulk save."""
    if isinstance(file_source, (str, Path)):
        with open(file_source, encoding="utf-8-sig") as file_handle:
            return _process_csv_stream(file_handle, batch_size)
    return _process_csv_stream(file_source, batch_size)


def _process_csv_stream(stream: IO[str], batch_size: int) -> ImportStats:
    reader = csv.DictReader(stream)
    if not reader.fieldnames:
        return ImportStats(rows_read=0, non_us_dropped=0, duplicates_dropped=0, rows_saved=0)

    header_map = {_normalize_key(col): col for col in reader.fieldnames}

    def get_value(row: dict[str, str], *keys: str, default: str = "") -> str:
        for key in keys:
            normalized = _normalize_key(key)
            if normalized in header_map:
                actual_header = header_map[normalized]
                val = row.get(actual_header, "").strip()
                if val:
                    return val
        return default

    rows_read = 0
    non_us_dropped = 0
    duplicates_dropped = 0
    best_stations: dict[int, Station] = {}

    for row in reader:
        rows_read += 1
        raw_state = get_value(row, "state").strip().upper()[:2]
        if raw_state not in US_STATES:
            non_us_dropped += 1
            continue

        raw_opis = get_value(row, "opis_id", "opis truckstop id", "truckstop_id", "id")
        raw_name = get_value(row, "name", "truckstop name")
        raw_address = get_value(row, "address")
        raw_city = get_value(row, "city")
        raw_rack = get_value(row, "rack_id", "rack id", default="0")
        raw_price = get_value(row, "retail_price", "retail price", "price", default="0.0")
        raw_lat = get_value(row, "latitude", "lat")
        raw_lon = get_value(row, "longitude", "lon", "lng")

        if not raw_opis or not raw_name:
            continue

        try:
            opis_id = int(float(raw_opis))
            rack_id = int(float(raw_rack))
            retail_price = Decimal(str(raw_price))
            latitude = float(raw_lat) if raw_lat else None
            longitude = float(raw_lon) if raw_lon else None
        except (ValueError, ArithmeticError):
            continue

        station = Station(
            opis_id=opis_id,
            name=raw_name,
            address=raw_address,
            city=raw_city,
            state=raw_state,
            rack_id=rack_id,
            retail_price=retail_price,
            latitude=latitude,
            longitude=longitude,
        )

        if opis_id in best_stations:
            duplicates_dropped += 1
            if retail_price < best_stations[opis_id].retail_price:
                best_stations[opis_id] = station
        else:
            best_stations[opis_id] = station

    stations_list = list(best_stations.values())
    with transaction.atomic():
        Station.objects.bulk_create(
            stations_list,
            batch_size=batch_size,
            update_conflicts=True,
            unique_fields=["opis_id"],
            update_fields=["name", "address", "city", "state", "rack_id", "retail_price"],
        )

    return ImportStats(
        rows_read=rows_read,
        non_us_dropped=non_us_dropped,
        duplicates_dropped=duplicates_dropped,
        rows_saved=len(stations_list),
    )


def ensure_geonames_data(cache_dir: Path | None = None) -> Path:
    """Ensure GeoNames US.zip is downloaded and cached locally."""
    target_dir = cache_dir or (Path(settings.BASE_DIR) / "data")
    target_dir.mkdir(parents=True, exist_ok=True)
    zip_path = target_dir / "US.zip"

    if not zip_path.exists():
        response = requests.get(GEONAMES_US_ZIP_URL, stream=True, timeout=60)
        response.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

    return zip_path


def load_geonames_city_map(zip_path: Path) -> dict[tuple[str, str], tuple[float, float]]:
    """Load populated places (feature class P) mapping normalized (city, state) to coordinates."""
    city_map: dict[tuple[str, str], tuple[int, float, float]] = {}

    with zipfile.ZipFile(zip_path) as z:
        with z.open("US.txt") as raw_f:
            f = TextIOWrapper(raw_f, encoding="utf-8", errors="ignore")
            for line in f:
                parts = line.split("\t")
                if len(parts) > 14 and parts[6] == "P":
                    city_norm = normalize_city(parts[1])
                    state = parts[10].strip().upper()
                    if not city_norm or not state:
                        continue
                    try:
                        pop = int(parts[14]) if parts[14].isdigit() else 0
                        lat = float(parts[4])
                        lon = float(parts[5])
                    except (ValueError, IndexError):
                        continue

                    key = (city_norm, state)
                    if key not in city_map or pop > city_map[key][0]:
                        city_map[key] = (pop, lat, lon)

    return {k: (v[1], v[2]) for k, v in city_map.items()}


def geocode_all_stations(
    zip_path: Path | None = None,
    batch_size: int = 1000,
) -> GeocodeStats:
    """Fill latitude/longitude for stations using offline GeoNames cache."""
    archive_path = zip_path or ensure_geonames_data()
    city_map = load_geonames_city_map(archive_path)

    stations = list(Station.objects.all())
    matched = 0
    unmatched = 0
    unmatched_pairs: list[tuple[str, str]] = []
    stations_to_update: list[Station] = []

    for station in stations:
        key = (normalize_city(station.city), station.state.strip().upper())
        if key in city_map:
            lat, lon = city_map[key]
            station.latitude = lat
            station.longitude = lon
            matched += 1
            stations_to_update.append(station)
        else:
            station.latitude = None
            station.longitude = None
            unmatched += 1
            pair = (station.city, station.state)
            if pair not in unmatched_pairs:
                unmatched_pairs.append(pair)
            stations_to_update.append(station)

    if stations_to_update:
        with transaction.atomic():
            Station.objects.bulk_update(
                stations_to_update,
                fields=["latitude", "longitude"],
                batch_size=batch_size,
            )

    return GeocodeStats(
        matched=matched,
        unmatched=unmatched,
        unmatched_pairs=unmatched_pairs,
    )
