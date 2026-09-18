import asyncio
from datetime import UTC, datetime, timedelta
from io import BytesIO
import pytest
from aiohttp import ClientResponseError
from aiohttp.client_reqrep import RequestInfo
from freezegun import freeze_time
from PIL import Image
from unittest.mock import AsyncMock, patch
from yarl import URL

from env_canada import ECMap
from env_canada.ec_cache import Cache
from env_canada.ec_geomet import geomet_url
from voluptuous import error
from syrupy.assertion import SnapshotAssertion


# Test fixtures
@pytest.fixture
def test_map():
    return ECMap(coordinates=(50, -100), layer="rain")


@pytest.fixture
def mock_capabilities_xml():
    """Mock capabilities XML response"""
    return b"""<?xml version="1.0" encoding="UTF-8"?>
    <WMS_Capabilities xmlns="http://www.opengis.net/wms">
        <Layer>
            <Name>RADAR_1KM_RRAI</Name>
            <Dimension name="time" units="ISO8601" default="2025-02-13T16:54:00Z">2025-02-13T13:54:00Z/2025-02-13T16:54:00Z/PT6M</Dimension>
            <Style>
                <Name>RADARURPPRECIPR</Name>
                <Title>Rain Style</Title>
            </Style>
        </Layer>
        <Layer>
            <Name>RADAR_1KM_RSNO</Name>
            <Dimension name="time" units="ISO8601" default="2025-02-13T16:54:00Z">2025-02-13T13:54:00Z/2025-02-13T16:54:00Z/PT6M</Dimension>
            <Style>
                <Name>RADARURPPRECIPS14</Name>
                <Title>Snow Style</Title>
            </Style>
        </Layer>
        <Layer>
            <Name>Radar_1km_SfcPrecipType</Name>
            <Dimension name="time" units="ISO8601" default="2025-02-13T16:54:00Z">2025-02-13T13:54:00Z/2025-02-13T16:54:00Z/PT6M</Dimension>
            <Style>
                <Name>SfcPrecipType_Dis</Name>
                <Title>Precipitation Type Style</Title>
            </Style>
            <Style>
                <Name>SfcPrecipType_Dis_Fr</Name>
                <Title>Style de type de precipitation</Title>
            </Style>
        </Layer>
    </WMS_Capabilities>"""


@pytest.fixture
def mock_capabilities_xml_with_extrapolation():
    """Mock capabilities XML including the rain radar extrapolation
    (nowcast) layer, mirroring GeoMet 3.0's real response shape: a `time`
    dimension picking up where the observed layer's ends, plus a
    `reference_time` dimension identifying the forecast model run."""
    return b"""<?xml version="1.0" encoding="UTF-8"?>
    <WMS_Capabilities xmlns="http://www.opengis.net/wms">
        <Layer>
            <Name>RADAR_1KM_RRAI</Name>
            <Dimension name="time" units="ISO8601" default="2025-02-13T16:54:00Z">2025-02-13T13:54:00Z/2025-02-13T16:54:00Z/PT6M</Dimension>
            <Style>
                <Name>RADARURPPRECIPR</Name>
                <Title>Rain Style</Title>
            </Style>
        </Layer>
        <Layer>
            <Name>Radar_1km_RainPrecipRate-Extrapolation</Name>
            <Dimension name="time" units="ISO8601" default="2025-02-13T17:00:00Z">2025-02-13T16:54:00Z/2025-02-13T18:00:00Z/PT6M</Dimension>
            <Dimension name="reference_time" units="ISO8601" default="2025-02-13T16:54:00Z" multipleValues="1">2025-02-13T13:54:00Z/2025-02-13T16:54:00Z/PT6M</Dimension>
            <Style>
                <Name>Radar-Rain_14colors</Name>
                <Title>Rain Style</Title>
            </Style>
        </Layer>
    </WMS_Capabilities>"""


@pytest.fixture
def mock_capabilities_xml_with_extrapolation_gap():
    """Mock capabilities XML where the extrapolation layer's own data
    window opens *after* "now" (16:54Z), unlike the real GeoMet response
    mocked above. The two layers refresh on independent schedules, so
    this gap can open depending on where the nowcast model's run cycle
    happens to be relative to the observed layer's latest frame."""
    return b"""<?xml version="1.0" encoding="UTF-8"?>
    <WMS_Capabilities xmlns="http://www.opengis.net/wms">
        <Layer>
            <Name>RADAR_1KM_RRAI</Name>
            <Dimension name="time" units="ISO8601" default="2025-02-13T16:54:00Z">2025-02-13T13:54:00Z/2025-02-13T16:54:00Z/PT6M</Dimension>
            <Style>
                <Name>RADARURPPRECIPR</Name>
                <Title>Rain Style</Title>
            </Style>
        </Layer>
        <Layer>
            <Name>Radar_1km_RainPrecipRate-Extrapolation</Name>
            <Dimension name="time" units="ISO8601" default="2025-02-13T17:12:00Z">2025-02-13T17:06:00Z/2025-02-13T18:00:00Z/PT6M</Dimension>
            <Dimension name="reference_time" units="ISO8601" default="2025-02-13T16:54:00Z" multipleValues="1">2025-02-13T13:54:00Z/2025-02-13T16:54:00Z/PT6M</Dimension>
            <Style>
                <Name>Radar-Rain_14colors</Name>
                <Title>Rain Style</Title>
            </Style>
        </Layer>
    </WMS_Capabilities>"""


@pytest.fixture
def mock_exception_xml():
    """Mock OGC ServiceExceptionReport, mirroring what GeoMet returns (HTTP
    200, not an error status) for a time GetCapabilities advertises but
    doesn't actually have data for."""
    return b"""<?xml version='1.0' encoding="utf-8"?>
    <ogc:ServiceExceptionReport version="1.3.0"
    xmlns:ogc="http://www.opengis.net/ogc">
    <ogc:ServiceException code="NoMatch" locator="time">time outside valid hours</ogc:ServiceException>
    </ogc:ServiceExceptionReport>"""


@pytest.fixture
def mock_image_bytes():
    """Mock PNG image bytes"""
    from PIL import Image

    img = Image.new("RGBA", (100, 100), (255, 0, 0, 128))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def mock_truncated_image_bytes(mock_image_bytes):
    """A PNG cut off partway through its pixel data - a connection dropped
    mid-response. The header is intact, so it passes Image.open(); only
    decoding the pixels reveals it."""
    return mock_image_bytes[: len(mock_image_bytes) // 2]


def _client_response_error(status):
    """Build the ClientResponseError that ClientSession(raise_for_status=True)
    raises for an HTTP error status."""
    url = URL(geomet_url)
    return ClientResponseError(
        RequestInfo(url, "GET", (), url), (), status=status, message="Server Error"
    )


class TestECMapInitialization:
    """Test ECMap initialization and validation"""

    def test_layer_validation_fast(self):
        """Test layer validation without network calls"""
        # Valid single layer
        map_obj = ECMap(coordinates=(50, -100), layer="rain")
        assert map_obj.layer == "rain"

        # Valid layer options
        map_obj = ECMap(coordinates=(50, -100), layer="snow")
        assert map_obj.layer == "snow"

        map_obj = ECMap(coordinates=(50, -100), layer="precip_type")
        assert map_obj.layer == "precip_type"

    def test_invalid_layer_combinations(self):
        """Test edge cases for layer validation"""
        # Invalid layer name
        with pytest.raises(error.MultipleInvalid):
            ECMap(coordinates=(50, -100), layer="invalid_layer")

    def test_parameter_validation(self):
        """Test comprehensive parameter validation"""
        # Invalid coordinates - longitude > 180 (this actually validates)
        with pytest.raises(error.MultipleInvalid):
            ECMap(coordinates=(50, 181), layer="rain")

        # Invalid radius - too small
        with pytest.raises(error.MultipleInvalid):
            ECMap(coordinates=(50, -100), radius=5, layer="rain")

        # Invalid opacity - > 100
        with pytest.raises(error.MultipleInvalid):
            ECMap(coordinates=(50, -100), layer_opacity=101, layer="rain")

        # Invalid opacity - < 0
        with pytest.raises(error.MultipleInvalid):
            ECMap(coordinates=(50, -100), layer_opacity=-1, layer="rain")

        # Invalid width/height
        with pytest.raises(error.MultipleInvalid):
            ECMap(coordinates=(50, -100), width=5, layer="rain")

        # Invalid fps - too low
        with pytest.raises(error.MultipleInvalid):
            ECMap(coordinates=(50, -100), fps=0, layer="rain")

        # Invalid fps - too high
        with pytest.raises(error.MultipleInvalid):
            ECMap(coordinates=(50, -100), fps=31, layer="rain")

        # Invalid loop_minutes - negative
        with pytest.raises(error.MultipleInvalid):
            ECMap(coordinates=(50, -100), loop_minutes=-1, layer="rain")

    def test_fps_and_loop_minutes_defaults(self):
        """Test that fps and loop_minutes default to previous behaviour"""
        map_obj = ECMap(coordinates=(50, -100), layer="rain")
        assert map_obj.fps == 5
        assert map_obj.loop_minutes == 0

    def test_fps_and_loop_minutes_custom(self):
        """Test that fps and loop_minutes can be customized"""
        map_obj = ECMap(coordinates=(50, -100), layer="rain", fps=10, loop_minutes=30)
        assert map_obj.fps == 10
        assert map_obj.loop_minutes == 30

    def test_interpolation_and_webp_defaults(self):
        """Test that interpolation and webp default to off"""
        map_obj = ECMap(coordinates=(50, -100), layer="rain")
        assert map_obj.interpolation is False
        assert map_obj.webp is False

    def test_interpolation_and_webp_custom(self):
        """Test that interpolation and webp can be enabled"""
        map_obj = ECMap(
            coordinates=(50, -100), layer="rain", interpolation=True, webp=True
        )
        assert map_obj.interpolation is True
        assert map_obj.webp is True

    def test_future_minutes_default(self):
        """Test that future_minutes defaults to 0 (no forward extension)"""
        map_obj = ECMap(coordinates=(50, -100), layer="rain")
        assert map_obj.future_minutes == 0

    def test_future_minutes_custom(self):
        """Test that future_minutes can be customized"""
        map_obj = ECMap(coordinates=(50, -100), layer="rain", future_minutes=30)
        assert map_obj.future_minutes == 30

    def test_future_minutes_invalid(self):
        """Test that a negative future_minutes is rejected"""
        with pytest.raises(error.MultipleInvalid):
            ECMap(coordinates=(50, -100), layer="rain", future_minutes=-1)

    def test_future_layer_lookup(self):
        """Test that only rain/snow have an extrapolation counterpart"""
        assert (
            ECMap(coordinates=(50, -100), layer="rain")._future_layer
            == "Radar_1km_RainPrecipRate-Extrapolation"
        )
        assert (
            ECMap(coordinates=(50, -100), layer="snow")._future_layer
            == "Radar_1km_SnowPrecipRate-Extrapolation"
        )
        assert ECMap(coordinates=(50, -100), layer="precip_type")._future_layer is None

    def test_bounding_box_pole_enclosing(self):
        """Coordinates/radius that enclose a pole should not raise, and should
        span the full longitude range with latitude clamped to +/-90"""
        map_obj = ECMap(coordinates=(90, -100), layer="rain")  # cos(90°) = 0
        _, lon_min, lat_max, lon_max = map_obj.bbox
        assert lat_max == 90.0
        assert lon_min == -180.0
        assert lon_max == 180.0

        # From issue #141: circle radius large enough to enclose the pole
        map_obj = ECMap(coordinates=(82.5, -62.3), radius=1000, layer="rain")
        _, lon_min, lat_max, lon_max = map_obj.bbox
        assert lat_max == 90.0
        assert lon_min == -180.0
        assert lon_max == 180.0

    def test_bounding_box_latitude_clamped(self):
        """Latitude should never exceed +/-90 even with a large radius near a pole"""
        map_obj = ECMap(coordinates=(80, 0), radius=2000, layer="rain")
        lat_min, _, lat_max, _ = map_obj.bbox
        assert lat_max == 90.0
        assert lat_min >= -90.0

    def test_edge_case_coordinates(self):
        """Test edge case coordinates that work with bounding box computation"""
        # Valid coordinates near the edges that don't cause math domain errors
        map_obj = ECMap(coordinates=(80, -179), layer="rain")
        assert map_obj.bbox is not None

        map_obj = ECMap(coordinates=(-80, 179), layer="rain")
        assert map_obj.bbox is not None

    def test_bbox_computation(self):
        """Test bounding box calculation"""
        map_obj = ECMap(coordinates=(50, -100), radius=100)
        assert len(map_obj.bbox) == 4
        assert all(isinstance(coord, float) for coord in map_obj.bbox)

        # Larger radius should create larger bbox
        map_obj_large = ECMap(coordinates=(50, -100), radius=200)
        large_bbox = map_obj_large.bbox
        small_bbox = map_obj.bbox

        # lat_min should be smaller, lat_max larger for bigger radius
        assert large_bbox[0] < small_bbox[0]  # lat_min
        assert large_bbox[2] > small_bbox[2]  # lat_max


class TestECMapImageGeneration:
    """Test ECMap image generation functionality"""

    @pytest.mark.slow
    @pytest.mark.parametrize(
        "init_parameters",
        [
            {"coordinates": (50, -100), "layer": "rain"},
            {"coordinates": (50, -100), "layer": "snow"},
            {"coordinates": (50, -100), "layer": "precip_type"},
            {"coordinates": (50, -100), "layer": "rain", "legend": False},
            {"coordinates": (50, -100), "layer": "rain", "timestamp": False},
        ],
    )
    def test_single_layer_generation(self, init_parameters):
        """Test single layer image generation"""
        map_obj = ECMap(**init_parameters)
        frame = asyncio.run(map_obj.get_latest_frame())
        image = Image.open(BytesIO(frame))
        assert image.format == "PNG"

    @pytest.mark.slow
    @pytest.mark.parametrize(
        "layer",
        [
            "rain",
            "snow",
            "precip_type",
        ],
    )
    def test_layer_generation(self, layer):
        """Test layer image generation"""
        map_obj = ECMap(coordinates=(50, -100), layer=layer)
        frame = asyncio.run(map_obj.get_latest_frame())
        image = Image.open(BytesIO(frame))
        assert image.format == "PNG"

    @pytest.mark.slow
    def test_get_dimensions(self, test_map):
        dimensions = asyncio.run(test_map._get_dimensions())
        assert isinstance(dimensions[0], datetime) and isinstance(
            dimensions[1], datetime
        )

    @pytest.mark.slow
    def test_get_latest_frame(self, test_map):
        frame = asyncio.run(test_map.get_latest_frame())
        image = Image.open(BytesIO(frame))
        assert image.format == "PNG"

    @pytest.mark.slow
    def test_get_loop(self, test_map):
        loop = asyncio.run(test_map.get_loop())
        image = Image.open(BytesIO(loop))
        assert image.format == "GIF" and image.is_animated

    @pytest.mark.slow
    def test_image_output_regression(self, snapshot: SnapshotAssertion):
        """Test image output hasn't changed unexpectedly"""
        map_obj = ECMap(
            coordinates=(50, -100), layer="rain", timestamp=False, legend=False
        )
        frame = asyncio.run(map_obj.get_latest_frame())

        # Create consistent image metadata for comparison
        image = Image.open(BytesIO(frame))
        image_data = {
            "format": image.format,
            "mode": image.mode,
            "size": image.size,
            "has_transparency": image.mode in ("RGBA", "LA")
            or "transparency" in image.info,
        }
        assert image_data == snapshot


class TestECMapErrorHandling:
    """Test ECMap error handling"""

    def test_network_error_handling(self):
        """Test graceful handling of network errors"""
        # Skip this test for now as it requires complex mocking
        pytest.skip("Network error handling test needs refinement")

    def test_missing_capabilities_handling(self):
        """Test handling when capabilities request fails"""
        # Skip this test for now as it requires complex mocking
        pytest.skip("Missing capabilities handling test needs refinement")

    @pytest.mark.parametrize(
        ("label", "body"),
        [
            ("mismatched tags", b"<invalid>xml</malformed>"),
            ("empty body", b""),
            ("not xml at all", b"502 Bad Gateway"),
        ],
    )
    @patch("env_canada.ec_map._get_resource")
    def test_unreadable_capabilities_does_not_raise(
        self, mock_get_resource, label, body, mock_image_bytes
    ):
        """Test that a GetCapabilities response that isn't usable XML - a
        proxy error page, a truncated body - degrades to "no image" rather
        than raising XMLSyntaxError out of update().

        This used to propagate out of whatever call asked for it, so a
        single bad response took down the whole integration. The response
        is also not cached, so the next poll asks again."""
        Cache.clear()

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return body
            return mock_image_bytes

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="rain")

        assert asyncio.run(map_obj._get_dimensions()) is None
        assert asyncio.run(map_obj.get_loop()) is None
        assert asyncio.run(map_obj.get_latest_frame()) is None

        # update() is what Home Assistant calls; it must not raise.
        asyncio.run(map_obj.update())
        assert map_obj.image is None

        # Nothing unusable was kept, so a recovered server is picked up
        # on the next poll rather than after the cache expires.
        assert Cache.get("capabilities-RADAR_1KM_RRAI") is None

    def test_capabilities_without_the_layer_returns_none(self):
        """A well-formed response that simply doesn't carry the layer is a
        different case: it parses, so it is cached rather than re-fetched."""
        Cache.clear()
        well_formed = b"""<?xml version="1.0" encoding="UTF-8"?>
        <WMS_Capabilities xmlns="http://www.opengis.net/wms"></WMS_Capabilities>"""

        def mock_response(url, params, bytes=True):
            return well_formed

        with patch("env_canada.ec_map._get_resource", side_effect=mock_response):
            map_obj = ECMap(coordinates=(50, -100), layer="rain")
            assert asyncio.run(map_obj._get_dimensions()) is None

        assert Cache.get("capabilities-RADAR_1KM_RRAI") is not None


class TestECMapCaching:
    """Test ECMap caching behavior"""

    @pytest.mark.slow
    @patch("env_canada.ec_map.Cache")
    def test_basemap_caching_behavior(self, mock_cache):
        """Test that basemap caching is used appropriately"""
        mock_cache.get.return_value = None
        mock_cache.add.return_value = b"cached_data"

        map_obj = ECMap(coordinates=(50, -100), layer="rain")

        # Should attempt to get from cache with location-specific key
        asyncio.run(map_obj._get_basemap())
        expected_cache_key = f"{map_obj._get_cache_prefix()}-basemap"
        mock_cache.get.assert_called_with(expected_cache_key)
        mock_cache.add.assert_called()

    def test_legend_generation(self):
        """Test that legend images are generated for all layers and languages"""
        from PIL import Image

        for layer in ("rain", "snow", "precip_type"):
            for lang in ("english", "french"):
                map_obj = ECMap(coordinates=(50, -100), layer=layer, language=lang)
                legend = map_obj._generate_legend()
                assert isinstance(legend, Image.Image)
                assert legend.width == map_obj.width
                assert legend.height > 0

    @pytest.mark.slow
    @patch("env_canada.ec_map.Cache")
    def test_layer_image_caching(self, mock_cache):
        """Test that layer images are cached"""
        mock_cache.get.return_value = None
        mock_cache.add.return_value = b"layer_data"

        map_obj = ECMap(coordinates=(50, -100), layer="rain")

        # Radar frames are only retained for a few hours, so ask the server
        # which times it currently has rather than hardcoding one.
        _, latest = asyncio.run(map_obj._get_dimensions())

        # Should cache layer images
        asyncio.run(map_obj._get_layer_image(latest))
        mock_cache.get.assert_called()
        mock_cache.add.assert_called()


class TestECMapMocked:
    """Test ECMap with mocked responses"""

    @patch("env_canada.ec_map._get_resource")
    def test_mocked_image_generation(
        self, mock_get_resource, mock_capabilities_xml, mock_image_bytes
    ):
        """Test image generation with mocked responses"""

        # Mock different responses based on URL patterns
        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml
            elif "GetMap" in str(params) or "GetLegendGraphic" in str(params):
                return mock_image_bytes
            else:
                return mock_image_bytes  # Basemap

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="rain")
        frame = asyncio.run(map_obj.get_latest_frame())

        # Should return valid image data
        assert isinstance(frame, bytes)
        image = Image.open(BytesIO(frame))
        assert image.format == "PNG"

    @patch("env_canada.ec_map._get_resource")
    def test_missing_frame_within_advertised_range_is_skipped(
        self,
        mock_get_resource,
        mock_capabilities_xml,
        mock_exception_xml,
        mock_image_bytes,
    ):
        """Test that a gap within GetCapabilities' own advertised time range
        - a real GeoMet behaviour, not just at the observed/extrapolation
        seam - doesn't crash get_loop(). Regression test: this used to
        propagate PIL.UnidentifiedImageError out of get_loop() and take the
        whole radar camera unavailable over a single missing frame."""
        Cache.clear()

        # Mocked capabilities span 13:54Z-16:54Z at 6-minute steps (31
        # frames); make the middle one a ServiceExceptionReport.
        missing_time = "2025-02-13T15:24:00Z"

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml
            if params.get("time") == missing_time:
                return mock_exception_xml
            return mock_image_bytes

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="rain")
        loop = asyncio.run(map_obj.get_loop())

        image = Image.open(BytesIO(loop))
        assert image.format == "GIF" and image.is_animated

        # The bad response isn't cached as if it were a real frame.
        cache_key = (
            f"{map_obj._get_cache_prefix()}-layer-RADAR_1KM_RRAI-{map_obj.colors}"
            f"-{map_obj.interpolation}-{map_obj.webp}-{missing_time}"
        )
        assert Cache.get(cache_key) is None

    @pytest.mark.parametrize(
        "error",
        [
            _client_response_error(500),
            _client_response_error(503),
            TimeoutError(),
        ],
        ids=["http_500", "http_503", "timeout"],
    )
    @patch("env_canada.ec_map._get_resource")
    def test_transient_fetch_error_on_one_frame_is_skipped(
        self, mock_get_resource, error, mock_capabilities_xml, mock_image_bytes
    ):
        """Test that a frame failing with an HTTP error status or a timeout
        is skipped like any other missing frame. Regression test: only
        ClientConnectorError was caught, but raise_for_status=True raises
        ClientResponseError and a timeout raises TimeoutError, so a single
        flaky frame aborted the whole loop."""
        Cache.clear()

        bad_time = "2025-02-13T15:24:00Z"

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml
            if params.get("time") == bad_time:
                raise error
            return mock_image_bytes

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="rain")
        loop = asyncio.run(map_obj.get_loop())

        image = Image.open(BytesIO(loop))
        assert image.format == "GIF" and image.is_animated

        cache_key = (
            f"{map_obj._get_cache_prefix()}-layer-RADAR_1KM_RRAI-{map_obj.colors}"
            f"-{map_obj.interpolation}-{map_obj.webp}-{bad_time}"
        )
        assert Cache.get(cache_key) is None

    @patch("env_canada.ec_map._get_resource")
    def test_truncated_image_is_skipped(
        self,
        mock_get_resource,
        mock_capabilities_xml,
        mock_image_bytes,
        mock_truncated_image_bytes,
    ):
        """Test that a frame whose image data is cut off partway through is
        skipped. Regression test: validation used Image.open(), which only
        reads the header, so a truncated image was cached as a real frame
        and then raised OSError inside the executor, aborting the loop."""
        Cache.clear()

        bad_time = "2025-02-13T15:24:00Z"

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml
            if params.get("time") == bad_time:
                return mock_truncated_image_bytes
            return mock_image_bytes

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="rain")
        loop = asyncio.run(map_obj.get_loop())

        image = Image.open(BytesIO(loop))
        assert image.format == "GIF" and image.is_animated

        cache_key = (
            f"{map_obj._get_cache_prefix()}-layer-RADAR_1KM_RRAI-{map_obj.colors}"
            f"-{map_obj.interpolation}-{map_obj.webp}-{bad_time}"
        )
        assert Cache.get(cache_key) is None

    @patch("env_canada.ec_map._get_resource")
    def test_skipped_frame_is_retried_once_the_server_recovers(
        self,
        mock_get_resource,
        mock_capabilities_xml,
        mock_exception_xml,
        mock_image_bytes,
    ):
        """Test that a skipped frame is re-requested on a later poll rather
        than leaving a basemap-only hole in the loop until the frame ages
        out. Regression test: the composite rendered without its radar layer
        was cached for the full 200 minutes, so a frame that failed once
        stayed blank for effectively its whole life in the loop, even though
        the server had recovered seconds later."""
        Cache.clear()

        missing_time = "2025-02-13T15:24:00Z"
        good_time = "2025-02-13T15:30:00Z"
        state = {"degraded": True, "requested": []}

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml
            if params.get("layers") != "CBMT":  # ignore the basemap
                state["requested"].append(params.get("time"))
            if params.get("time") == missing_time and state["degraded"]:
                return mock_exception_xml
            return mock_image_bytes

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="rain")

        with freeze_time("2025-02-13 17:00:00") as frozen:
            asyncio.run(map_obj.get_loop())
            assert missing_time in state["requested"]

            # The server recovers, and Home Assistant polls again.
            state["degraded"] = False
            state["requested"].clear()
            frozen.tick(timedelta(minutes=5))
            loop = asyncio.run(map_obj.get_loop())

            # The frame that failed is retried...
            assert missing_time in state["requested"]
            # ...while frames that succeeded are still served from cache.
            assert good_time not in state["requested"]

            assert Image.open(BytesIO(loop)).format == "GIF"

            # The retried frame is now cached as a real frame.
            cache_key = (
                f"{map_obj._get_cache_prefix()}-layer-RADAR_1KM_RRAI-{map_obj.colors}"
                f"-{map_obj.interpolation}-{map_obj.webp}-{missing_time}"
            )
            assert Cache.get(cache_key) is not None

    @patch("env_canada.ec_map._get_resource")
    def test_stale_capabilities_do_not_request_a_window_that_has_slid(
        self, mock_get_resource, mock_capabilities_xml, mock_image_bytes
    ):
        """Test that a capabilities response held across a publication
        doesn't make the loop ask for a time the server has since dropped.

        GeoMet slides a fixed-width window forward, so the `start` in a
        cached response goes out of range as soon as a new step is
        published - which is what produces the `code="NoMatch"` exception
        in #160. The loop's oldest frame moves forward with the window
        instead."""
        Cache.clear()

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml
            return mock_image_bytes

        mock_get_resource.side_effect = mock_response

        # The window ends 16:54Z and a step is published a couple of minutes
        # after the instant it's stamped with, so 16:56Z is a realistic time
        # to have read the capabilities. The loop is then built at 17:01Z,
        # after the grid instant 17:00Z has gone by but while the response
        # is still cached - a Home Assistant poll landing mid-lifetime.
        read_at = datetime(2025, 2, 13, 16, 56, tzinfo=UTC)
        with freeze_time(read_at) as frozen:
            map_obj = ECMap(coordinates=(50, -100), layer="rain")
            asyncio.run(map_obj._get_dimensions())
            frozen.tick(timedelta(minutes=5))

            requested = []

            def capture(url, params, bytes=True):
                if "GetCapabilities" in str(params):
                    return mock_capabilities_xml
                if params.get("layers") != "CBMT":
                    requested.append(params["time"])
                return mock_image_bytes

            mock_get_resource.side_effect = capture
            asyncio.run(map_obj.get_loop())

        # The advertised start is 13:54Z; one step has been published since
        # the capabilities were read, so it is no longer served.
        assert "2025-02-13T13:54:00Z" not in requested
        assert min(requested) == "2025-02-13T14:00:00Z"
        # The newest frame is still asked for.
        assert max(requested) == "2025-02-13T16:54:00Z"

    @patch("env_canada.ec_map._get_resource")
    def test_loop_minutes_truncates_frames(
        self, mock_get_resource, mock_capabilities_xml, mock_image_bytes
    ):
        """Test that loop_minutes limits the loop to recent frames only"""
        Cache.clear()

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml
            return mock_image_bytes

        mock_get_resource.side_effect = mock_response

        # Mocked capabilities span 13:54Z-16:54Z (3 hours) at 6-minute intervals:
        # 31 frames requested.
        full_loop = ECMap(coordinates=(50, -100), layer="rain")
        with patch.object(
            full_loop,
            "_create_composite_image",
            wraps=full_loop._create_composite_image,
        ) as mock_create:
            asyncio.run(full_loop.get_loop())
            assert mock_create.call_count == 31

        # Truncated to the last 30 minutes: 6 frames requested.
        short_loop = ECMap(coordinates=(50, -100), layer="rain", loop_minutes=30)
        with patch.object(
            short_loop,
            "_create_composite_image",
            wraps=short_loop._create_composite_image,
        ) as mock_create:
            asyncio.run(short_loop.get_loop())
            assert mock_create.call_count == 6

    @patch("env_canada.ec_map._get_resource")
    def test_loop_and_future_minutes_stay_grid_aligned(
        self,
        mock_get_resource,
        mock_capabilities_xml_with_extrapolation,
        mock_image_bytes,
    ):
        """Test that loop_minutes/future_minutes values that aren't a
        multiple of the layer's time-grid step (e.g. 65 minutes on a
        6-minute grid) don't shift the whole loop off-grid.

        Regression test: `now - timedelta(minutes=65)` lands on a timestamp
        GeoMet doesn't recognize, and since every later frame is stepped by
        a fixed 6-minute interval from that misaligned anchor, EVERY frame
        in the loop - not just one - came back as "no data" and the
        rendered animation had no radar overlay in any frame at all.
        """
        Cache.clear()
        requested_times = []

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml_with_extrapolation
            if "time" in params:
                requested_times.append(params["time"])
            return mock_image_bytes

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(
            coordinates=(50, -100), layer="rain", loop_minutes=65, future_minutes=65
        )
        loop = asyncio.run(map_obj.get_loop())
        assert loop is not None

        assert requested_times, "expected at least one GetMap request"
        for time_str in requested_times:
            minute = int(time_str[14:16])
            assert minute % 6 == 0, f"{time_str} is off the 6-minute grid"

    @patch("env_canada.ec_map._get_resource")
    def test_fps_controls_frame_duration(
        self, mock_get_resource, mock_capabilities_xml
    ):
        """Test that the fps instance attribute is used by update()/get_loop()"""
        Cache.clear()

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml
            # Vary pixel colour per frame so GIF frames aren't coalesced
            colour = (hash(params.get("time", "")) % 255, 0, 0, 128)
            img = Image.new("RGBA", (100, 100), colour)
            buf = BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="rain", fps=10)
        asyncio.run(map_obj.update())
        image = Image.open(BytesIO(map_obj.image))
        assert image.info["duration"] == 100

    @patch("env_canada.ec_map._get_resource")
    def test_interpolation_adds_wms_param(
        self, mock_get_resource, mock_capabilities_xml, mock_image_bytes
    ):
        """Test that interpolation=True adds the INTERPOLATION WMS param"""
        Cache.clear()

        captured_params = []

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml
            captured_params.append(params)
            return mock_image_bytes

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="rain", interpolation=True)
        asyncio.run(map_obj._get_layer_image(datetime(2025, 2, 13, 16, 54, 0)))

        assert any(p.get("interpolation") == "true" for p in captured_params)

    @patch("env_canada.ec_map._get_resource")
    def test_interpolation_defaults_omit_wms_param(
        self, mock_get_resource, mock_capabilities_xml, mock_image_bytes
    ):
        """Test that interpolation=False (default) omits the WMS param"""
        Cache.clear()

        captured_params = []

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml
            captured_params.append(params)
            return mock_image_bytes

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="rain")
        asyncio.run(map_obj._get_layer_image(datetime(2025, 2, 13, 16, 54, 0)))

        assert all("interpolation" not in p for p in captured_params)

    @patch("env_canada.ec_map._get_resource")
    def test_webp_requests_webp_format_from_wms(
        self, mock_get_resource, mock_capabilities_xml, mock_image_bytes
    ):
        """Test that webp=True requests format=image/webp from the WMS layer"""
        Cache.clear()

        captured_params = []

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml
            captured_params.append(params)
            return mock_image_bytes

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="rain", webp=True)
        asyncio.run(map_obj._get_layer_image(datetime(2025, 2, 13, 16, 54, 0)))

        assert any(p.get("format") == "image/webp" for p in captured_params)

    @patch("env_canada.ec_map._get_resource")
    def test_webp_default_requests_png_format_from_wms(
        self, mock_get_resource, mock_capabilities_xml, mock_image_bytes
    ):
        """Test that webp=False (default) keeps requesting format=image/png"""
        Cache.clear()

        captured_params = []

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml
            captured_params.append(params)
            return mock_image_bytes

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="rain")
        asyncio.run(map_obj._get_layer_image(datetime(2025, 2, 13, 16, 54, 0)))

        assert all(p.get("format") == "image/png" for p in captured_params)

    @patch("env_canada.ec_map._get_resource")
    def test_webp_latest_frame_output_format(
        self, mock_get_resource, mock_capabilities_xml, mock_image_bytes
    ):
        """Test that webp=True returns get_latest_frame() as WebP, end-to-end"""
        Cache.clear()

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml
            return mock_image_bytes

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="rain", webp=True)
        frame = asyncio.run(map_obj.get_latest_frame())
        image = Image.open(BytesIO(frame))
        assert image.format == "WEBP"

    @patch("env_canada.ec_map._get_resource")
    def test_webp_loop_output_format(self, mock_get_resource, mock_capabilities_xml):
        """Test that webp=True produces an animated WebP instead of a GIF"""
        Cache.clear()

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml
            # Vary pixel colour per frame so frames aren't coalesced
            colour = (hash(params.get("time", "")) % 255, 0, 0, 128)
            img = Image.new("RGBA", (100, 100), colour)
            buf = BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="rain", webp=True)
        loop = asyncio.run(map_obj.get_loop())
        image = Image.open(BytesIO(loop))
        assert image.format == "WEBP" and image.is_animated

    @patch("env_canada.ec_map._get_resource")
    def test_webp_default_loop_output_format(
        self, mock_get_resource, mock_capabilities_xml
    ):
        """Test that webp=False (default) keeps producing an animated GIF"""
        Cache.clear()

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml
            # Vary pixel colour per frame so frames aren't coalesced
            colour = (hash(params.get("time", "")) % 255, 0, 0, 128)
            img = Image.new("RGBA", (100, 100), colour)
            buf = BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="rain")
        loop = asyncio.run(map_obj.get_loop())
        image = Image.open(BytesIO(loop))
        assert image.format == "GIF" and image.is_animated

    @patch("env_canada.ec_map._get_resource")
    def test_future_minutes_extends_loop_with_extrapolation_layer(
        self,
        mock_get_resource,
        mock_capabilities_xml_with_extrapolation,
        mock_image_bytes,
    ):
        """Test that future_minutes pulls in extrapolation frames past
        the observed range, using the same styles, while past/current
        frames keep using the observed layer."""
        Cache.clear()

        captured_params = []

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml_with_extrapolation
            if params.get("layers") != "CBMT":  # skip the basemap request
                captured_params.append(params)
            return mock_image_bytes

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="rain", future_minutes=30)
        asyncio.run(map_obj.get_loop())

        # Observed layer: 13:54Z-16:54Z, unchanged. Extrapolation layer
        # extends the loop from 16:54Z to 17:24Z (min(18:00Z, 16:54+30min)).
        observed = [p for p in captured_params if p.get("layers") == "RADAR_1KM_RRAI"]
        future = [
            p
            for p in captured_params
            if p.get("layers") == "Radar_1km_RainPrecipRate-Extrapolation"
        ]
        assert len(observed) == 30  # 13:54Z..16:48Z every 6 min
        assert len(future) == 6  # 16:54Z..17:24Z every 6 min

        # Both use the same rain style prefix/colour count.
        assert all(p.get("styles") == "Radar-Rain_14colors" for p in observed)
        assert all(p.get("styles") == "Radar-Rain_14colors" for p in future)

        # Only future frames are pinned to a single forecast model run.
        assert all("dim_reference_time" not in p for p in observed)
        assert all(
            p.get("dim_reference_time") == "2025-02-13T16:54:00Z" for p in future
        )

    @patch("env_canada.ec_map._get_resource")
    def test_future_minutes_handles_gap_before_extrapolation_data_starts(
        self,
        mock_get_resource,
        mock_capabilities_xml_with_extrapolation_gap,
        mock_image_bytes,
    ):
        """Test that frames falling after "now" but before the
        extrapolation layer's own data window opens don't request an
        invalid time from the observed layer (regression test: this used
        to raise PIL.UnidentifiedImageError, since the WMS server doesn't
        return an image for an out-of-range observed-layer time)."""
        Cache.clear()

        captured_params = []

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml_with_extrapolation_gap
            if params.get("layers") != "CBMT":  # skip the basemap request
                captured_params.append(params)
            return mock_image_bytes

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="rain", future_minutes=30)
        # Must not raise. Observed ends 16:54Z, extrapolation starts
        # 17:06Z - frames at 17:00Z fall in that 12-minute gap.
        asyncio.run(map_obj.get_loop())

        observed = [p for p in captured_params if p.get("layers") == "RADAR_1KM_RRAI"]
        future = [
            p
            for p in captured_params
            if p.get("layers") == "Radar_1km_RainPrecipRate-Extrapolation"
        ]

        # The observed layer is never asked for a time past "now".
        assert all(p["time"] <= "2025-02-13T16:54:00Z" for p in observed)
        # The extrapolation layer is never asked for a time before its
        # own data actually starts - the 17:00Z frame is clamped to it.
        assert all(p["time"] >= "2025-02-13T17:06:00Z" for p in future)
        assert any(p["time"] == "2025-02-13T17:06:00Z" for p in future)

    @patch("env_canada.ec_map._get_resource")
    def test_future_minutes_default_does_not_extend_loop(
        self,
        mock_get_resource,
        mock_capabilities_xml_with_extrapolation,
        mock_image_bytes,
    ):
        """Test that future_minutes=0 (default) never requests the
        extrapolation layer, even when one exists for the chosen layer."""
        Cache.clear()

        captured_params = []

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml_with_extrapolation
            if params.get("layers") != "CBMT":  # skip the basemap request
                captured_params.append(params)
            return mock_image_bytes

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="rain")
        asyncio.run(map_obj.get_loop())

        assert all(p.get("layers") == "RADAR_1KM_RRAI" for p in captured_params)
        assert len(captured_params) == 31

    @patch("env_canada.ec_map._get_resource")
    def test_future_minutes_ignored_for_precip_type(
        self, mock_get_resource, mock_capabilities_xml, mock_image_bytes
    ):
        """Test that precip_type (no extrapolation layer available) ignores
        future_minutes and produces the same loop as without it."""
        Cache.clear()

        captured_params = []

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml
            if params.get("layers") != "CBMT":  # skip the basemap request
                captured_params.append(params)
            return mock_image_bytes

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="precip_type", future_minutes=45)
        asyncio.run(map_obj.get_loop())

        assert all(
            p.get("layers") == "Radar_1km_SfcPrecipType" for p in captured_params
        )
        assert all("dim_reference_time" not in p for p in captured_params)
        assert len(captured_params) == 31

    @patch("env_canada.ec_map._get_resource")
    def test_get_latest_frame_unaffected_by_future_minutes(
        self,
        mock_get_resource,
        mock_capabilities_xml_with_extrapolation,
        mock_image_bytes,
    ):
        """Test that get_latest_frame() always returns the latest real
        observation, never a forecast frame, regardless of future_minutes."""
        Cache.clear()

        captured_params = []

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml_with_extrapolation
            if params.get("layers") != "CBMT":  # skip the basemap request
                captured_params.append(params)
            return mock_image_bytes

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="rain", future_minutes=30)
        asyncio.run(map_obj.get_latest_frame())

        assert len(captured_params) == 1
        assert captured_params[0]["layers"] == "RADAR_1KM_RRAI"
        assert captured_params[0]["time"] == "2025-02-13T16:54:00Z"
        assert "dim_reference_time" not in captured_params[0]

    @patch("env_canada.ec_map._get_resource")
    def test_future_boundary_does_not_leak_between_calls(
        self,
        mock_get_resource,
        mock_capabilities_xml_with_extrapolation,
        mock_image_bytes,
    ):
        """Test that a get_loop() call's future-extension state doesn't
        leak into a later get_latest_frame() call on the same instance."""
        Cache.clear()

        captured_params = []

        def mock_response(url, params, bytes=True):
            if "GetCapabilities" in str(params):
                return mock_capabilities_xml_with_extrapolation
            if params.get("layers") != "CBMT":  # skip the basemap request
                captured_params.append(params)
            return mock_image_bytes

        mock_get_resource.side_effect = mock_response

        map_obj = ECMap(coordinates=(50, -100), layer="rain", future_minutes=30)
        asyncio.run(map_obj.get_loop())
        assert map_obj._future_boundary is not None

        captured_params.clear()
        asyncio.run(map_obj.get_latest_frame())

        assert captured_params[0]["layers"] == "RADAR_1KM_RRAI"
        assert "dim_reference_time" not in captured_params[0]


# Legacy tests for backward compatibility
def test_validate_layers():
    """Legacy test - kept for backward compatibility"""
    map_obj = ECMap(coordinates=(50, -100), layer="rain")
    assert map_obj.layer == "rain"

    map_obj = ECMap(coordinates=(50, -100), layer="snow")
    assert map_obj.layer == "snow"

    # Invalid layer
    with pytest.raises(error.MultipleInvalid):
        ECMap(coordinates=(50, -100), layer="invalid_layer")


@patch("env_canada.ec_map._get_resource")
def test_frame_interval_follows_the_time_dimension(
    mock_get_resource, mock_capabilities_xml
):
    """Frame spacing comes from the step the layer advertises, rather than
    assuming the 6 minutes the radar layers happen to use."""
    Cache.clear()
    mock_get_resource.side_effect = AsyncMock(
        return_value=mock_capabilities_xml.replace(b"PT6M", b"PT10M")
    )

    map_obj = ECMap(coordinates=(50, -100), layer="rain")
    assert map_obj._image_interval == timedelta(minutes=6)  # default before lookup

    asyncio.run(map_obj._get_dimensions())
    assert map_obj._image_interval == timedelta(minutes=10)


@patch("env_canada.ec_map._get_resource")
def test_frame_interval_falls_back_without_a_step(
    mock_get_resource, mock_capabilities_xml
):
    """A dimension of "start/end" with no step keeps the default spacing."""
    Cache.clear()
    mock_get_resource.side_effect = AsyncMock(
        return_value=mock_capabilities_xml.replace(b"/PT6M", b"")
    )

    map_obj = ECMap(coordinates=(50, -100), layer="rain")
    asyncio.run(map_obj._get_dimensions())
    assert map_obj._image_interval == timedelta(minutes=6)
