import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from freezegun import freeze_time
from syrupy.assertion import SnapshotAssertion

from env_canada import ECWeather, ec_weather
from env_canada.ec_cache import Cache


@pytest.mark.parametrize(
    "init_parameters",
    [
        {"coordinates": (50, -100)},
        {"station_id": "ON/s0000430"},
        {"station_id": "s0000430"},
        {"station_id": "430"},
    ],
)
def test_ecweather(init_parameters):
    weather = ECWeather(**init_parameters)
    assert isinstance(weather, ECWeather)


def setup_test(args) -> tuple[ECWeather, AsyncMock]:
    resp = AsyncMock(status_code=200)
    side_effects = []

    # For new URL structure, we need to mock both directory listing and weather file
    # First response: sites CSV
    # Second response: directory listing HTML
    # Third response: weather XML

    for k, v in args.items():
        if k == "sites":
            with open(v) as file:
                side_effects.append(file.read())
        elif k == "forecast":
            # Add mock directory listing HTML before the forecast
            station = args.get("station", "ON/s0000430")
            if "/" in station:
                _, station_num = station.split("/")
                station_num = station_num.replace("s0000", "")
            else:
                station_num = (
                    station.replace("s0000", "") if "s0000" in station else station
                )

            # Mock directory listing HTML with a sample file
            mock_html = f"""<html><body>
            <a href="20250206T120000.000Z_MSC_CitypageWeather_s0000{station_num.zfill(3)}_en.xml">file</a>
            </body></html>"""
            side_effects.append(mock_html)

            # Add the actual forecast XML
            with open(v) as file:
                side_effects.append(file.read())
        elif k == "exception":
            side_effects.append(v)
        elif k == "status_code":
            resp.status_code = v

    resp.text.side_effect = side_effects

    return (ECWeather(station_id=args["station"]), resp)


@pytest.mark.asyncio
async def test_weather_retrieved_weather_updates_ok(snapshot: SnapshotAssertion):
    ecw, resp = setup_test(
        {
            "station": "ON/s0000430",
            "sites": "tests/fixtures/site_list.csv",
            "forecast": "tests/fixtures/weather.xml",
        }
    )

    with patch("aiohttp.ClientSession.get", AsyncMock(return_value=resp)):
        with freeze_time("2025-02-06 00:00"):
            await ecw.update()

    assert ecw == snapshot


@pytest.mark.asyncio
async def test_weather_exception_on_old_forecast_data():
    ecw, resp = setup_test(
        {
            "station": "ON/s0000430",
            "sites": "tests/fixtures/site_list.csv",
            "forecast": "tests/fixtures/weather.xml",
        }
    )

    with patch("aiohttp.ClientSession.get", AsyncMock(return_value=resp)):
        with freeze_time("2025-02-06 03:00"):
            with pytest.raises(ec_weather.ECWeatherUpdateFailed):
                await ecw.update()


@pytest.mark.asyncio
async def test_weather_exception_returns_cached_data():
    ecw, resp = setup_test(
        {
            "station": "ON/s0000430",
            "sites": "tests/fixtures/site_list.csv",
            "forecast": "tests/fixtures/weather.xml",
        }
    )

    # Do the initial update with a good response
    with patch("aiohttp.ClientSession.get", AsyncMock(return_value=resp)):
        with freeze_time("2025-02-06 00:00"):
            await ecw.update()

    with patch("aiohttp.ClientSession.get", side_effect=TimeoutError):
        with freeze_time("2025-02-06 00:00"):
            await ecw.update()

    assert ecw.metadata.cache_returned_on_update == 1

    with patch("aiohttp.ClientSession.get", side_effect=TimeoutError):
        with freeze_time("2025-02-06 00:00"):
            await ecw.update()

    assert ecw.metadata.cache_returned_on_update == 2

    # Move date into future, should not return cached data now
    with patch("aiohttp.ClientSession.get", side_effect=TimeoutError):
        with freeze_time("2025-02-06 11:42"):
            with pytest.raises(ec_weather.ECWeatherUpdateFailed):
                await ecw.update()

    assert ecw.metadata.cache_returned_on_update == 0


@pytest.mark.slow
def test_get_ec_sites():
    sites = asyncio.run(ec_weather.get_ec_sites())
    assert len(sites) > 0


@pytest.mark.slow
def test_update_ec_weather():
    ecw, _ = setup_test({"station": "ON/s0000430"})
    asyncio.run(ecw.update())
    assert ecw.conditions


@pytest.mark.parametrize(
    "station_input,expected_result",
    [
        ("ON/s0000430", "ON/s0000430"),
        ("s0000430", "s0000430"),
        ("430", "430"),
        ("1", "1"),
        ("99", "99"),
    ],
)
def test_validate_station(station_input, expected_result):
    """Test that station validation returns the input unchanged when valid."""
    result = ec_weather.validate_station(station_input)
    assert result == expected_result


@pytest.mark.asyncio
async def test_station_id_formats_create_tuples():
    """Test that different station ID formats result in proper tuples."""
    test_cases = [
        ("ON/s0000430", ("ON", "430")),
        ("s0000430", ("ON", "430")),  # Should find ON from site data
        ("430", ("ON", "430")),  # Should find ON from site data
    ]

    for station_input, expected_tuple in test_cases:
        ecw, resp = setup_test(
            {
                "station": station_input,
                "sites": "tests/fixtures/site_list.csv",
                "forecast": "tests/fixtures/weather.xml",
            }
        )

        with patch("aiohttp.ClientSession.get", AsyncMock(return_value=resp)):
            with freeze_time("2025-02-06 00:00"):
                await ecw.update()

        assert ecw.station_tuple == expected_tuple
        assert ecw.lat is not None
        assert ecw.lon is not None


@pytest.mark.asyncio
async def test_home_assistant_compatibility():
    """Test that station_id remains a string for Home Assistant compatibility."""
    ecw, resp = setup_test(
        {
            "station": "ON/s0000430",
            "sites": "tests/fixtures/site_list.csv",
            "forecast": "tests/fixtures/weather.xml",
        }
    )

    with patch("aiohttp.ClientSession.get", AsyncMock(return_value=resp)):
        with freeze_time("2025-02-06 00:00"):
            await ecw.update()

    # station_id should remain a string for external API compatibility
    assert isinstance(ecw.station_id, str)
    assert ecw.station_id == "ON/s0000430"

    # Internal tuple should be available via property
    assert ecw.station_tuple == ("ON", "430")


@pytest.mark.asyncio
async def test_alerts_keep_previous_wfs_records_while_wfs_fetch_in_flight():
    """`update()` parses the citypage XML alerts as the fallback for a failed
    WFS fetch. It used to assign them to `alerts` before awaiting the WFS
    fetch, so a reader landing during that await (Home Assistant's
    `get_alerts` action, issue #182) got XML-shaped records: no `text`,
    `area` or `alert_code`, and citypage-format dates. Hold the WFS response
    open on a second poll and check that `alerts` still has the previous
    poll's WFS records until the new ones arrive."""
    with open("tests/fixtures/alerts_wfs.json") as f:
        wfs = json.load(f)
    with open("tests/fixtures/weather.xml") as f:
        weather_xml = f.read()
    listing = """<html><body>
    <a href="20250206T120000.000Z_MSC_CitypageWeather_s0000430_en.xml">file</a>
    </body></html>"""

    ecw, resp = setup_test(
        {
            "station": "ON/s0000430",
            "sites": "tests/fixtures/site_list.csv",
            "forecast": "tests/fixtures/weather.xml",
        }
    )
    resp.json.return_value = wfs

    with patch("aiohttp.ClientSession.get", AsyncMock(return_value=resp)):
        with freeze_time("2025-02-06 00:00"):
            await ecw.update()

            first_poll = ecw.alerts
            assert first_poll["advisories"]["value"][0]["alert_code"] == "AD"

            Cache.clear()
            resp.text.side_effect = [listing, weather_xml]
            wfs_requested = asyncio.Event()
            release_wfs = asyncio.Event()

            async def slow_wfs_json():
                wfs_requested.set()
                await release_wfs.wait()
                return wfs

            resp.json.side_effect = slow_wfs_json
            poll = asyncio.create_task(ecw.update())
            await wfs_requested.wait()

            assert ecw.alerts is first_poll
            advisory = ecw.alerts["advisories"]["value"][0]
            assert advisory["alert_code"] == "AD"
            assert advisory["date"] == "2025-02-05T23:07:00Z"

            release_wfs.set()
            await poll

    assert ecw.alerts is not first_poll
    assert ecw.alerts["advisories"]["value"][0]["alert_code"] == "AD"


@pytest.mark.asyncio
async def test_alerts_fall_back_to_xml_records_when_wfs_fetch_fails():
    """The XML alerts are now assigned only in the WFS failure branch, so
    check that branch still publishes them: a failed WFS fetch must leave
    the citypage alerts in place rather than the previous or empty ones."""
    ecw, resp = setup_test(
        {
            "station": "ON/s0000430",
            "sites": "tests/fixtures/site_list.csv",
            "forecast": "tests/fixtures/weather.xml",
        }
    )
    resp.json.side_effect = TimeoutError

    with patch("aiohttp.ClientSession.get", AsyncMock(return_value=resp)):
        with freeze_time("2025-02-06 00:00"):
            await ecw.update()

    advisories = ecw.alerts["advisories"]["value"]
    assert len(advisories) == 1
    assert advisories[0]["title"] == "Weather Advisory"
    assert advisories[0]["expiryTime"] == "20250206150755"
    assert "alert_code" not in advisories[0]
    assert ecw.alert_features == []
