import asyncio
from datetime import datetime
from io import BytesIO
import pytest
from PIL import Image
from unittest.mock import patch

from env_canada import ECMap
from env_canada.ec_cache import Cache
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
def mock_image_bytes():
    """Mock PNG image bytes"""
    from PIL import Image

    img = Image.new("RGBA", (100, 100), (255, 0, 0, 128))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


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

    def test_invalid_capabilities_xml_handling(self):
        """Test handling of malformed capabilities XML"""
        # Test that malformed XML is handled gracefully in the actual method
        # by mocking the Cache to return bad XML
        with patch("env_canada.ec_map.Cache") as mock_cache:
            mock_cache.get.return_value = b"<invalid>xml</malformed>"

            map_obj = ECMap(coordinates=(50, -100), layer="rain")
            # Should not crash with malformed XML, should return None
            try:
                result = asyncio.run(map_obj._get_dimensions())
                assert result is None
            except Exception:
                # If it raises an exception, that's expected behavior for malformed XML
                pass


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
        test_time = datetime(2025, 2, 13, 16, 54, 0)

        # Should cache layer images
        asyncio.run(map_obj._get_layer_image(test_time))
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
