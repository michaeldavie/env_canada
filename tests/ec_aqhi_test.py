import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from env_canada import ECAirQuality, ec_aqhi

# Captured from dd.weather.gc.ca for Ottawa (zone "ont", region "FEVNT").
FIXTURES = Path(__file__).parent / "fixtures"
OBSERVATION = (FIXTURES / "aqhi_observation.xml").read_bytes()
FORECAST = (FIXTURES / "aqhi_forecast.xml").read_bytes()


def serve(observation=OBSERVATION, forecast=FORECAST):
    """Answer ClientSession.get by URL, as the service would, rather than
    by the order the requests happen to be made in."""

    async def get(url, **kwargs):
        body = observation if "/AQ_OBS_" in url else forecast
        response = AsyncMock()
        response.read.return_value = body
        return response

    return patch("aiohttp.ClientSession.get", side_effect=get)


def ottawa(language="EN"):
    return ECAirQuality(zone_id="ont", region_id="FEVNT", language=language)


@pytest.mark.slow
def test_get_aqhi_regions():
    regions = asyncio.run(ec_aqhi.get_aqhi_regions("EN"))
    assert len(regions) > 0


@pytest.mark.parametrize(
    "init_parameters",
    [{"coordinates": (50, -100)}, {"zone_id": "ont", "region_id": "FEVNT"}],
)
def test_ecaqhi(init_parameters):
    aqhi = ECAirQuality(**init_parameters)
    assert isinstance(aqhi, ECAirQuality)


@pytest.fixture()
def test_aqhi():
    return ECAirQuality(coordinates=(49.91, -97.24))


@pytest.mark.slow
def test_update(test_aqhi):
    asyncio.run(test_aqhi.update())
    assert isinstance(test_aqhi.current, float) or test_aqhi.current is None
    assert all([isinstance(p, str) for p in test_aqhi.forecasts["daily"].keys()])
    assert all([isinstance(f, int) for f in test_aqhi.forecasts["daily"].values()])
    assert all([isinstance(d, datetime) for d in test_aqhi.forecasts["hourly"].keys()])
    assert all([isinstance(f, int) for f in test_aqhi.forecasts["hourly"].values()])


def test_update_reads_the_observation_and_forecast():
    aqhi = ottawa()
    with serve():
        asyncio.run(aqhi.update())

    assert aqhi.current == 2.1
    assert aqhi.current_timestamp == datetime(2026, 9, 27, 20, 0, tzinfo=UTC)
    assert aqhi.region_name == aqhi.metadata.location == "Ottawa"
    assert aqhi.forecasts["daily"] == {
        "Tonight": 2,
        "Tomorrow": 2,
        "Tomorrow Night": 2,
        "Day 3": 2,
    }
    hourly = aqhi.forecasts["hourly"]
    assert len(hourly) == 37
    assert next(iter(hourly)) == datetime(2026, 9, 27, 21, 0, tzinfo=UTC)


def test_french_reads_the_french_period_names():
    aqhi = ottawa(language="FR")
    with serve():
        asyncio.run(aqhi.update())

    assert list(aqhi.forecasts["daily"]) == [
        "Ce soir et cette nuit",
        "Demain",
        "Demain soir et nuit",
        "Jour 3",
    ]


def test_forecast_without_a_period_in_the_requested_language_is_skipped():
    """The period name was taken from whichever <period> matched the
    language, in a variable left over from the loop. A forecast with no
    match raised UnboundLocalError when it came first, and was otherwise
    filed under the previous forecast's name. Synthetic: the captured
    forecast with the first English period removed."""
    forecast = FORECAST.replace(
        b'<period forecastName="Tonight" lang="EN">Sunday night</period>', b""
    )
    aqhi = ottawa()
    with serve(forecast=forecast):
        asyncio.run(aqhi.update())

    assert list(aqhi.forecasts["daily"]) == ["Tomorrow", "Tomorrow Night", "Day 3"]


@pytest.mark.parametrize(
    ("observation", "expected_current"),
    [
        (
            OBSERVATION.replace(
                b"<airQualityHealthIndex>2.1</airQualityHealthIndex>", b""
            ),
            None,
        ),
        (
            OBSERVATION.replace(
                b"<airQualityHealthIndex>2.1</airQualityHealthIndex>",
                b"<airQualityHealthIndex/>",
            ),
            None,
        ),
        (
            OBSERVATION.replace(
                b'<region nameEn="Ottawa" nameFr="Ottawa">FEVNT</region>', b""
            ),
            2.1,
        ),
    ],
    ids=["no index", "empty index", "no region"],
)
def test_observation_missing_a_value_does_not_raise(
    caplog, observation, expected_current
):
    """An empty index raised TypeError from float(None), a missing region
    raised AttributeError, and a missing index reached a debug message that
    formats the index with %d and so fails on None whenever debug logging is
    on. Synthetic: the captured observation with one element removed or
    emptied."""
    caplog.set_level(logging.DEBUG, logger="env_canada.ec_aqhi")
    aqhi = ottawa()
    with serve(observation=observation):
        asyncio.run(aqhi.update())

    assert aqhi.current == expected_current


def test_unreadable_response_is_treated_as_a_failed_request():
    """A truncated or non-XML body raised lxml's XMLSyntaxError out of
    update(), which is not the xml.etree ParseError Home Assistant catches.
    It is now handled like a failed request: update() returns normally and
    the values from the last good response stay."""
    aqhi = ottawa()
    with serve():
        asyncio.run(aqhi.update())
    with serve(observation=OBSERVATION[:200], forecast=b"502 Bad Gateway"):
        asyncio.run(aqhi.update())

    assert aqhi.current == 2.1
    assert len(aqhi.forecasts["hourly"]) == 37


def test_each_update_replaces_the_previous_forecast():
    """Forecasts were added to on every update and never cleared, so the
    hourly dict grew for as long as the object lived. When there is no
    current observation, Home Assistant shows the dict's first value -
    which was the first hour of the first forecast ever fetched.
    Synthetic: the captured forecast reissued three hours later."""
    later = FORECAST
    for stamp in (b"20260927210000", b"20260927220000", b"20260927230000"):
        later = later.replace(
            b'<hourlyForecast UTCTime="' + stamp + b'">2</hourlyForecast>', b""
        )
    later = later.replace(
        b'<period forecastName="Tonight" lang="EN">Sunday night</period>',
        b'<period forecastName="Overnight" lang="EN">Sunday night</period>',
    )

    aqhi = ottawa()
    with serve():
        asyncio.run(aqhi.update())
    with serve(forecast=later):
        asyncio.run(aqhi.update())

    hourly = aqhi.forecasts["hourly"]
    assert len(hourly) == 34
    assert next(iter(hourly)) == datetime(2026, 9, 28, 0, 0, tzinfo=UTC)
    assert "Tonight" not in aqhi.forecasts["daily"]
    assert "Overnight" in aqhi.forecasts["daily"]
