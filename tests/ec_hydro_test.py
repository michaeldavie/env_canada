import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol

from env_canada import ECHydro, ec_hydro

# Captured from dd.weather.gc.ca and trimmed to the header plus the last six
# readings of each station: 02KF005 (Ottawa River at Britannia) reports both
# water level and discharge, 02DD006 (Lake Nipissing) only a water level and
# 05RE001 (Poplar River) only a discharge. The station list is trimmed to
# eight of its 2171 rows. Both keep the service's bytes, including CRLF line
# endings and the leading space in the readings' first header.
FIXTURES = Path(__file__).parent / "fixtures" / "hydro"
SITES = (FIXTURES / "station_list.csv").read_bytes()
READINGS = {
    station: (FIXTURES / f"readings_{station}.csv").read_bytes()
    for station in ("02KF005", "02DD006", "05RE001")
}


def serve(sites=SITES, readings=READINGS):
    """Answer ClientSession.get by URL, as the service would."""

    async def get(url, **kwargs):
        response = AsyncMock()
        if url == ec_hydro.SITE_LIST_URL:
            response.read.return_value = sites
        else:
            station = url.split("/")[-1].split("_")[1]
            response.read.return_value = readings[station]
        return response

    return patch("aiohttp.ClientSession.get", side_effect=get)


def britannia():
    return ECHydro(province="ON", station="02KF005")


@pytest.mark.slow
def test_get_hydro_sites():
    sites = asyncio.run(ec_hydro.get_hydro_sites())
    assert len(sites) > 0


@pytest.mark.parametrize(
    "init_parameters",
    [{"coordinates": (50, -100)}, {"province": "ON", "station": "02KF005"}],
)
@pytest.mark.slow
def test_echydro(init_parameters):
    hydro = ECHydro(**init_parameters)
    assert isinstance(hydro, ECHydro)
    asyncio.run(hydro.update())
    assert isinstance(hydro.timestamp, datetime)
    assert isinstance(hydro.measurements["water_level"]["value"], float)
    if hydro.measurements.get("discharge"):
        assert isinstance(hydro.measurements["discharge"]["value"], float)


def test_station_list_is_parsed_into_typed_records():
    """The service's column headers are bilingual ("Name / Nom"); the parser
    keeps the English half, and coordinates become floats."""
    with serve():
        sites = asyncio.run(ec_hydro.get_hydro_sites())

    assert len(sites) == 8
    britannia_site = next(s for s in sites if s["ID"] == "02KF005")
    assert britannia_site == {
        "ID": "02KF005",
        "Name": "OTTAWA RIVER AT BRITANNIA",
        "Latitude": 45.35125,
        "Longitude": -75.82666,
        "Prov": "ON",
        "Timezone": "UTC-05:00",
    }


def test_station_list_skips_rows_without_coordinates():
    """The parser is meant to ignore bad site data, but tested for a missing
    field (None, from a short row) and not for an empty one, so a station
    with blank coordinates raised ValueError from float("") and took
    closest_site() down with it. Synthetic: the captured list with one
    station's coordinates emptied."""
    sites = SITES.replace(b"47.360780,-68.324890", b",")
    with serve(sites=sites):
        parsed = asyncio.run(ec_hydro.get_hydro_sites())

    assert len(parsed) == 7
    assert "01AD004" not in {s["ID"] for s in parsed}


def test_closest_site_is_nearest_by_distance():
    with serve():
        closest = asyncio.run(ec_hydro.closest_site(45.4, -75.7))

    assert closest["ID"] == "02KF005"
    assert closest["Prov"] == "ON"


def test_coordinates_resolve_to_the_nearest_station_and_read_it():
    hydro = ECHydro(coordinates=(45.4, -75.7))
    with serve():
        asyncio.run(hydro.update())

    assert (hydro.province, hydro.station) == ("ON", "02KF005")
    assert hydro.location == "Ottawa River At Britannia"
    assert hydro.measurements["water_level"]["value"] == 57.753


def test_update_reports_the_latest_reading():
    hydro = britannia()
    with serve():
        asyncio.run(hydro.update())

    assert hydro.measurements == {
        "water_level": {"label": "Water Level", "value": 57.753, "unit": "m"},
        "discharge": {"label": "Discharge", "value": 511.0, "unit": "m³/s"},
    }
    assert hydro.timestamp == datetime(
        2026, 9, 30, 15, 35, tzinfo=timezone(timedelta(hours=-5))
    )


@pytest.mark.parametrize(
    ("province", "station", "expected"),
    [
        ("ON", "02DD006", {"water_level": 5.712}),
        ("MB", "05RE001", {"discharge": 25.0}),
    ],
    ids=["water level only", "discharge only"],
)
def test_station_reporting_only_one_measurement(province, station, expected):
    """Many stations measure only a level (lakes) or only a flow. The empty
    column is left out rather than reported as zero."""
    hydro = ECHydro(province=province, station=station)
    with serve():
        asyncio.run(hydro.update())

    assert {k: v["value"] for k, v in hydro.measurements.items()} == expected


def test_a_reading_of_zero_is_a_reading():
    """Dry streams report a discharge of 0, which must not be mistaken for
    the empty string of a missing measurement. Synthetic: the captured
    Britannia row with its discharge set to the 0 the service writes for a
    dry station."""
    readings = dict(READINGS)
    readings["02KF005"] = READINGS["02KF005"].replace(b",511,", b",0,")
    hydro = britannia()
    with serve(readings=readings):
        asyncio.run(hydro.update())

    assert hydro.measurements["discharge"]["value"] == 0.0


def test_a_measurement_that_stops_being_reported_is_dropped():
    """measurements was only ever added to, so a station whose discharge
    sensor went offline kept reporting the last discharge it had, stamped
    with the new reading's timestamp. Synthetic: the Britannia station
    updated once normally, then from a response with no discharge."""
    readings = dict(READINGS)
    readings["02KF005"] = READINGS["02KF005"].replace(b",511,", b",,")
    hydro = britannia()
    with serve():
        asyncio.run(hydro.update())
    with serve(readings=readings):
        asyncio.run(hydro.update())

    assert list(hydro.measurements) == ["water_level"]


def test_update_with_no_readings_leaves_the_timestamp_unset():
    """A file with a header and no rows is what a station between
    publications looks like. Synthetic: the captured header alone."""
    header = READINGS["02KF005"].splitlines(keepends=True)[0]
    hydro = britannia()
    with serve(readings={"02KF005": header}):
        asyncio.run(hydro.update())

    assert hydro.measurements == {}
    assert hydro.timestamp is None


@pytest.mark.parametrize(
    "init_parameters",
    [
        {},
        {"province": "ON"},
        {"station": "02KF005"},
        {"province": "Ontario", "station": "02KF005"},
        {"province": "ON", "station": "02KF00"},
        {"coordinates": (95, -75)},
    ],
    ids=[
        "nothing",
        "province alone",
        "station alone",
        "province not a two-letter code",
        "station not seven characters",
        "latitude out of range",
    ],
)
def test_init_rejects_what_cannot_identify_a_station(init_parameters):
    with pytest.raises(vol.Invalid):
        ECHydro(**init_parameters)
