import asyncio

import pytest

from env_canada import ECHistorical


@pytest.mark.parametrize(
    "init_parameters", [
        {"station_id": 48370, "year": 2021},
        {"station_id": 48370, "year": 2021, "language": "english"},
        {"station_id": 48370, "year": 2021, "language": "french"},
        {"station_id": 48370, "year": 2021, "format": "csv"},
        {"station_id": 48370, "year": 2021, "format": "xml"}
    ]
)
def test_echistorical(init_parameters):
    weather = ECHistorical(**init_parameters)
    assert isinstance(weather, ECHistorical)

@pytest.fixture()
def test_historical():
    return ECHistorical(station_id=48370, year=2021)

def test_update(test_historical):
    asyncio.run(test_historical.update())
    assert test_historical.metadata
    assert test_historical.station_data
