import asyncio
from datetime import date, datetime
from io import BytesIO
from unittest.mock import patch

import pytest
from PIL import Image

from env_canada import ECRadar

CAPABILITIES = b"""<?xml version="1.0" encoding="UTF-8"?>
<WMS_Capabilities xmlns="http://www.opengis.net/wms">
    <Layer>
        <Name>RADAR_1KM_RRAI</Name>
        <Dimension name="time" units="ISO8601">2025-02-13T16:42:00Z/2025-02-13T16:54:00Z/PT6M</Dimension>
    </Layer>
</WMS_Capabilities>"""


@pytest.mark.slow
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


@pytest.mark.slow
def test_get_dimensions(test_radar):
    dimensions = asyncio.run(test_radar._get_dimensions())
    assert isinstance(dimensions[0], datetime) and isinstance(dimensions[1], datetime)


@pytest.mark.slow
def test_get_latest_frame(test_radar):
    frame = asyncio.run(test_radar.get_latest_frame())
    image = Image.open(BytesIO(frame))
    assert image.format == "PNG"


@pytest.mark.slow
def test_get_loop(test_radar):
    loop = asyncio.run(test_radar.get_loop())
    image = Image.open(BytesIO(loop))
    assert image.format == "GIF" and image.is_animated


def test_set_precip_type(test_radar):
    test_radar.precip_type = "auto"
    assert test_radar.precip_type[0] == "auto"

    if date.today().month in range(4, 11):
        assert test_radar.precip_type[1] == "rain"
    else:
        assert test_radar.precip_type[1] == "snow"


def test_get_legend_returns_an_image():
    """ECRadar._get_legend used to forward to ECMap._get_legend, which doesn't
    exist - the method it wants is _generate_legend."""
    radar = ECRadar(coordinates=(50, -100), precip_type="rain")
    legend = radar._get_legend()
    assert isinstance(legend, Image.Image)


@patch("env_canada.ec_map._get_resource")
def test_timestamp_and_image_follow_the_underlying_map(mock_get_resource):
    """ECRadar copied the timestamp from the ECMap it wraps once, when it
    was constructed - before the map had fetched anything - so it was
    always None. The image was only kept current by update()."""
    buf = BytesIO()
    Image.new("RGBA", (100, 100), (255, 0, 0, 128)).save(buf, format="PNG")
    png = buf.getvalue()

    def mock_response(url, params, bytes=True, timeout=None):
        return CAPABILITIES if params.get("request") == "GetCapabilities" else png

    mock_get_resource.side_effect = mock_response
    radar = ECRadar(coordinates=(50, -100), precip_type="rain", width=100, height=100)

    asyncio.run(radar.get_latest_frame())
    assert radar.timestamp == "2025-02-13T16:54:00+00:00"

    asyncio.run(radar.update())
    assert radar.image is not None
    assert radar.image == radar._map.image
