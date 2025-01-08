import asyncio
from datetime import datetime

import pytest

from env_canada import ECHistorical, ECHistoricalRange


@pytest.mark.parametrize(
    "init_parameters",
    [
        {"station_id": 48370, "year": 2021},
        {"station_id": 48370, "year": 2021, "language": "english"},
        {"station_id": 48370, "year": 2021, "language": "french"},
        {"station_id": 48370, "year": 2021, "format": "csv"},
        {"station_id": 48370, "year": 2021, "format": "xml"},
        {
            "station_id": 48370,
            "year": 2021,
            "month": 5,
            "format": "xml",
            "timeframe": 1,
        },
        {"station_id": 48370, "year": 2021, "format": "csv", "timeframe": 1},
    ],
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


@pytest.mark.parametrize(
    "station_id,timeframe,startdate,enddate",
    [
        (10761, "daily", datetime(2022, 1, 1), datetime(2022, 12, 31)),
        (10761, "daily", datetime(2022, 1, 1), datetime(2023, 12, 31)),
        (10761, "daily", datetime(2022, 1, 1), datetime(2022, 11, 30)),
        (10761, "hourly", datetime(2022, 1, 1), datetime(2022, 2, 3, 23, 59, 59)),
    ],
)
def test_historical_number_values(station_id, timeframe, startdate, enddate):
    if timeframe == "daily":
        number_of_data_points_per_day = 1
    else:
        number_of_data_points_per_day = 24
    number_of_data_points = (
        (enddate - startdate).days + 1
    ) * number_of_data_points_per_day
    ec = ECHistoricalRange(
        station_id=station_id, timeframe=timeframe, daterange=(startdate, enddate)
    )
    data = ec.get_data()
    rows, _ = data.shape
    assert rows == number_of_data_points
