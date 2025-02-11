import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from freezegun import freeze_time
from syrupy.assertion import SnapshotAssertion

from env_canada import ECWeather, ec_weather


@pytest.mark.parametrize(
    "init_parameters", [{"coordinates": (50, -100)}, {"station_id": "ON/s0000430"}]
)
def test_ecweather(init_parameters):
    weather = ECWeather(**init_parameters)
    assert isinstance(weather, ECWeather)


def setup_test(args) -> tuple[ECWeather, AsyncMock]:
    resp = AsyncMock(status_code=200)
    side_effects = []

    for k, v in args.items():
        if k == "forecast":
            with open(v) as file:
                side_effects.append(file.read())
        elif k == "sites":
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


@pytest.mark.slow
def test_get_ec_sites():
    sites = asyncio.run(ec_weather.get_ec_sites())
    assert len(sites) > 0


@pytest.mark.slow
def test_update_ec_weather():
    ecw, _ = setup_test({"station": "ON/s0000430"})
    asyncio.run(ecw.update())
    assert ecw.conditions
