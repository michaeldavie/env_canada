import asyncio
from datetime import datetime
from io import BytesIO
import pytest
from PIL import Image
from unittest.mock import AsyncMock, patch

from env_canada import ECMap
from voluptuous import error


@pytest.mark.slow
@pytest.mark.parametrize(
    "init_parameters",
    [
        {"coordinates": (50, -100), "layers": ["rain"]},
        {"coordinates": (50, -100), "layers": ["snow"]},
        {"coordinates": (50, -100), "layers": ["rain"], "legend": False},
        {"coordinates": (50, -100), "layers": ["rain"], "timestamp": False},
    ],
)
def test_ecmap(init_parameters):
    map_obj = ECMap(**init_parameters)
    frame = asyncio.run(map_obj.get_latest_frame())
    image = Image.open(BytesIO(frame))
    assert image.format == "PNG"


@pytest.fixture
def test_map():
    return ECMap(coordinates=(50, -100), layers=["rain"])


@pytest.mark.slow
def test_get_dimensions(test_map):
    dimensions = asyncio.run(test_map._get_dimensions("rain"))
    assert isinstance(dimensions[0], datetime) and isinstance(dimensions[1], datetime)


@pytest.mark.slow
def test_get_latest_frame(test_map):
    frame = asyncio.run(test_map.get_latest_frame())
    image = Image.open(BytesIO(frame))
    assert image.format == "PNG"


@pytest.mark.slow
def test_get_loop(test_map):
    loop = asyncio.run(test_map.get_loop())
    image = Image.open(BytesIO(loop))
    assert image.format == "GIF" and image.is_animated


def test_validate_layers():
    map_obj = ECMap(coordinates=(50, -100), layers=["rain"])
    assert map_obj.layers == ["rain"]

    map_obj = ECMap(coordinates=(50, -100), layers=["rain", "snow"])
    assert map_obj.layers == ["rain", "snow"]

    # Invalid layer
    with pytest.raises(error.MultipleInvalid):
        ECMap(coordinates=(50, -100), layers=["invalid_layer"])
