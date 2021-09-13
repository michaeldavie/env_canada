import asyncio
from datetime import datetime, date
from io import BytesIO
from PIL import Image

import pytest

from env_canada import ECRadar


@pytest.mark.parametrize(
    "init_parameters",
    [
        {"coordinates": (50, -100), "precip_type": "snow", "legend": False},
        {"coordinates": (50, -100), "precip_type": "rain", "timestamp": False},
        {"coordinates": (50, -100)},
        {"coordinates": (50, -100), "precip_type": None},
    ],
)
def test_ecradar(init_parameters):
    radar = ECRadar(**init_parameters)
    frame = asyncio.run(radar.get_latest_frame())
    image = Image.open(BytesIO(frame))
    assert image.format == "PNG"


@pytest.fixture
def test_radar():
    return ECRadar(coordinates=(50, -100))


def test_get_dimensions(test_radar):
    dimensions = asyncio.run(test_radar._get_dimensions())
    assert isinstance(dimensions[0], datetime) and isinstance(dimensions[1], datetime)


def test_get_latest_frame(test_radar):
    frame = asyncio.run(test_radar.get_latest_frame())
    image = Image.open(BytesIO(frame))
    assert image.format == "PNG"


def test_get_loop(test_radar):
    loop = asyncio.run(test_radar.get_loop())
    image = Image.open(BytesIO(loop))
    assert image.format == "GIF" and image.is_animated


def test_set_precip_type(test_radar):
    test_radar.precip_type = "auto"
    if date.today().month in range(4, 11):
        assert test_radar.precip_type == "rain"
    else:
        assert test_radar.precip_type == "snow"
