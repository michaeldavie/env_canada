import asyncio
from datetime import datetime

import pytest

from env_canada import ECAirQuality


@pytest.mark.parametrize("init_parameters", [
    {"coordinates": (50, -100)},
    {"zone_id": 'ont', "region_id": 'FEVNT'}
])
def test_ecaqhi(init_parameters):
    aqhi = ECAirQuality(**init_parameters)
    assert isinstance(aqhi, ECAirQuality)


@pytest.fixture()
def test_aqhi():
    return ECAirQuality(coordinates=(50, -100))


def test_update(test_aqhi):
    asyncio.run(test_aqhi.update())
    assert isinstance(test_aqhi.current, float)
    assert all([isinstance(p, str) for p in test_aqhi.forecasts["daily"].keys()])
    assert all([isinstance(f, int) for f in test_aqhi.forecasts["daily"].values()])
    assert all([isinstance(d, datetime) for d in test_aqhi.forecasts["hourly"].keys()])
    assert all([isinstance(f, int) for f in test_aqhi.forecasts["hourly"].values()])
