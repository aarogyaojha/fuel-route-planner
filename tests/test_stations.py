import zipfile
from decimal import Decimal
from io import BytesIO, StringIO
from pathlib import Path

import pytest

from apps.stations.models import Station
from apps.stations.services import (
    geocode_all_stations,
    import_stations_from_csv,
    normalize_city,
)


def test_normalize_city():
    assert normalize_city("St. Louis") == "saint louis"
    assert normalize_city("st.louis") == "saint louis"
    assert normalize_city("Ft. Worth") == "fort worth"
    assert normalize_city("Mt. Pleasant") == "mount pleasant"
    assert normalize_city("Winston-Salem") == "winston salem"
    assert normalize_city("Coeur d'Alene") == "coeur dalene"
    assert normalize_city("  LOS   ANGELES  ") == "los angeles"


@pytest.mark.django_db
def test_station_creation_and_str():
    station = Station.objects.create(
        opis_id=101,
        name="Pilot Travel Center",
        address="123 Highway Ave",
        city="Dallas",
        state="TX",
        rack_id=45,
        retail_price=Decimal("3.45900"),
        latitude=32.7767,
        longitude=-96.7970,
    )
    assert station.opis_id == 101
    assert str(station) == "Pilot Travel Center - Dallas, TX ($3.45900)"


@pytest.mark.django_db
def test_import_stations_filtering_and_deduplication():
    csv_data = """OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price
1001,Love's Travel Stop,100 Interstate Rd,Memphis,TN,12,3.19900
1002,Flying J,200 Turnpike St,Toronto,ON,14,3.09900
1003,Shell Express,500 Route 1,Dallas,TX,10,3.50000
1003,Shell Express Lower,500 Route 1,Dallas,TX,10,3.25000
1003,Shell Express Higher,500 Route 1,Dallas,TX,10,3.80000
"""
    stream = StringIO(csv_data)
    stats = import_stations_from_csv(stream)

    assert stats.rows_read == 5
    assert stats.non_us_dropped == 1
    assert stats.duplicates_dropped == 2
    assert stats.rows_saved == 2

    st_1003 = Station.objects.get(opis_id=1003)
    assert st_1003.retail_price == Decimal("3.25000")
    assert not Station.objects.filter(state="ON").exists()

    stream_rerun = StringIO(csv_data)
    stats_rerun = import_stations_from_csv(stream_rerun)
    assert stats_rerun.rows_saved == 2
    assert Station.objects.count() == 2


@pytest.mark.django_db
def test_geocode_stations_with_fixture(tmp_path: Path):
    Station.objects.create(
        opis_id=2001,
        name="St. Louis Stop",
        address="100 Grand",
        city="St. Louis",
        state="MO",
        rack_id=1,
        retail_price=Decimal("3.10"),
    )
    Station.objects.create(
        opis_id=2002,
        name="Small Town Stop",
        address="50 Main St",
        city="Springfield",
        state="IL",
        rack_id=2,
        retail_price=Decimal("3.20"),
    )
    Station.objects.create(
        opis_id=2003,
        name="Unknown Place Stop",
        address="99 Nowhere",
        city="Atlantis",
        state="TX",
        rack_id=3,
        retail_price=Decimal("3.30"),
    )

    # In GeoNames format (19 tab-separated columns):
    # col 1: name, col 4: lat, col 5: lon, col 6: feature_class, col 10: admin1, col 14: population
    geonames_lines = [
        "1\tSaint Louis\tSaint Louis\t\t38.6270\t-90.1994\tP\tPPL\tUS\t\tMO\t\t\t\t300000\t\t\t\t",
        "2\tSpringfield\tSpringfield\t\t39.0000\t-89.0000\tP\tPPL\tUS\t\tIL\t\t\t\t500\t\t\t\t",
        "3\tSpringfield\tSpringfield\t\t39.7817\t-89.6501\tP\tPPL\tUS\t\tIL\t\t\t\t115000\t\t\t\t",
        "4\tLake Springfield\tLake Springfield\t\t39.7500\t-89.6000\tH\tLK\tUS\t\tIL\t\t\t\t0\t\t\t\t",
    ]
    zip_bytes = BytesIO()
    with zipfile.ZipFile(zip_bytes, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("US.txt", "\n".join(geonames_lines).encode("utf-8"))

    fixture_zip = tmp_path / "US.zip"
    fixture_zip.write_bytes(zip_bytes.getvalue())

    stats = geocode_all_stations(zip_path=fixture_zip)
    assert stats.matched == 2
    assert stats.unmatched == 1
    assert stats.unmatched_pairs == [("Atlantis", "TX")]

    st_louis = Station.objects.get(opis_id=2001)
    assert st_louis.latitude == pytest.approx(38.6270)
    assert st_louis.longitude == pytest.approx(-90.1994)

    # Springfield should take the higher population record (115,000 vs 500)
    springfield = Station.objects.get(opis_id=2002)
    assert springfield.latitude == pytest.approx(39.7817)
    assert springfield.longitude == pytest.approx(-89.6501)

    atlantis = Station.objects.get(opis_id=2003)
    assert atlantis.latitude is None
    assert atlantis.longitude is None
