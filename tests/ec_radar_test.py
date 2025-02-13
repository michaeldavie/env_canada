import asyncio
import base64
from datetime import date, datetime
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image
from syrupy.assertion import SnapshotAssertion

from env_canada import ECRadar


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


@pytest.mark.asyncio
async def test_get_radar_image_with_mock_data(test_radar, snapshot: SnapshotAssertion):
    """
    This is still technically a slow test, in that it uses a lot of CPU.
    Still useful as it is a complete test of the entire radar code
    except for going to the network.
    """

    def mock_get_resource(_, params, bytes=True):
        fname = f"tests/fixtures/radar/{params['request']}_{params.get('time', '')}"
        with open(fname, "rb") as f:
            return f.read()

    with patch(
        "env_canada.ec_radar._get_resource", AsyncMock(side_effect=mock_get_resource)
    ):
        await test_radar.update()
        # Bit hacky. This gets around syrupy not comparing snapshots as equal while
        # binary data is present. Changing the image to b64 should not compromise the test.
        breakpoint()
        test_radar.image = base64.b64encode(test_radar.image).decode()

    assert test_radar == snapshot
