import asyncio

import pytest

from env_canada import ec_weather, ECWeather


def test_get_ec_sites():
    sites = asyncio.run(ec_weather.get_ec_sites())
    assert len(sites) > 0


@pytest.mark.parametrize(
    "init_parameters", [{"coordinates": (50, -100)}, {"station_id": "ON/s0000430"}]
)
def test_ecweather(init_parameters):
    weather = ECWeather(**init_parameters)
    assert isinstance(weather, ECWeather)

@pytest.fixture()
def no_conditions():
    return ECWeather(station_id="ON/s0000773")

def test_update_no_conditions(no_conditions):
    asyncio.run(no_conditions.update())
    assert no_conditions.conditions == {}
    assert no_conditions.daily_forecasts
    assert no_conditions.hourly_forecasts

@pytest.fixture()
def with_conditions():
    return ECWeather(station_id="ON/s0000430")

def test_update_with_conditions(with_conditions):
    asyncio.run(with_conditions.update())
    assert with_conditions.conditions