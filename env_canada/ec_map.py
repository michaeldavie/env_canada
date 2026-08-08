import asyncio
import logging
import math
from datetime import timedelta
from io import BytesIO

import dateutil.parser
import voluptuous as vol
from aiohttp import ClientSession
from aiohttp.client_exceptions import ClientConnectorError
from lxml import etree as et
from PIL import Image, ImageDraw

from .constants import USER_AGENT
from .ec_cache import Cache
from .ec_legend import generate_legend, load_font

LOG = logging.getLogger(__name__)

ATTRIBUTION = {
    "english": "Data provided by Environment Canada",
    "french": "Données fournies par Environnement Canada",
}

__all__ = ["ECMap"]

# Natural Resources Canada

basemap_url = "https://maps.geogratis.gc.ca/wms/CBMT"
basemap_params = {
    "service": "wms",
    "version": "1.3.0",
    "request": "GetMap",
    "layers": "CBMT",
    "styles": "",
    "CRS": "epsg:4326",
    "format": "image/png",
}


# Environment Canada

# Common WMS layers available from Environment Canada

wms_layers = {
    "rain": "RADAR_1KM_RRAI",
    "snow": "RADAR_1KM_RSNO",
    "precip_type": "Radar_1km_SfcPrecipType",
}

# Radar extrapolation (nowcast) counterparts, used to extend a loop into the
# future. Styles are identical to the observed layer above, so no separate
# entry is needed in wms_style_prefixes. precip_type has no such layer.
wms_layers_extrapolation = {
    "rain": "Radar_1km_RainPrecipRate-Extrapolation",
    "snow": "Radar_1km_SnowPrecipRate-Extrapolation",
}

# Layers with a colour-count style choice (name prefix used to build the
# WMS STYLES value, e.g. "Radar-Rain_8colors"). precip_type has only one
# style, so it's omitted here and always uses the WMS server default.
wms_style_prefixes = {
    "rain": "Radar-Rain",
    "snow": "Radar-Snow",
}


geomet_url = "https://geo.weather.gc.ca/geomet"
capabilities_params = {
    "lang": "en",
    "service": "WMS",
    "version": "1.3.0",
    "request": "GetCapabilities",
}
wms_namespace = {"wms": "http://www.opengis.net/wms"}
dimension_xpath = './/wms:Layer[wms:Name="{layer}"]/wms:Dimension[@name="{dim}"]'
map_params = {
    "service": "WMS",
    "version": "1.3.0",
    "request": "GetMap",
    "crs": "EPSG:4326",
    "format": "image/png",
    "transparent": "true",
}
image_interval = timedelta(minutes=6)

timestamp_label = {
    "rain": {"english": "Rain", "french": "Pluie"},
    "snow": {"english": "Snow", "french": "Neige"},
    "precip_type": {"english": "Precipitation", "french": "Précipitation"},
}


def _compute_bounding_box(distance, latittude, longitude):
    """
    Modified from https://gist.github.com/alexcpn/f95ae83a7ee0293a5225
    """
    latittude = math.radians(latittude)
    longitude = math.radians(longitude)

    distance_from_point_km = distance
    angular_distance = distance_from_point_km / 6371.01

    lat_min = max(-math.pi / 2, latittude - angular_distance)
    lat_max = min(math.pi / 2, latittude + angular_distance)

    cos_latittude = math.cos(latittude)
    ratio = math.sin(angular_distance) / cos_latittude if cos_latittude else math.inf

    if abs(ratio) >= 1:
        # Circle encloses a pole: longitude spans the full range.
        lon_min = -math.pi
        lon_max = math.pi
    else:
        delta_longitude = math.asin(ratio)
        lon_min = longitude - delta_longitude
        lon_max = longitude + delta_longitude
    lon_min = round(math.degrees(lon_min), 5)
    lat_max = round(math.degrees(lat_max), 5)
    lon_max = round(math.degrees(lon_max), 5)
    lat_min = round(math.degrees(lat_min), 5)

    return lat_min, lon_min, lat_max, lon_max


async def _get_resource(url, params, bytes=True):
    async with ClientSession(raise_for_status=True) as session:
        response = await session.get(
            url=url, params=params, headers={"User-Agent": USER_AGENT}
        )
        if bytes:
            return await response.read()
        return await response.text()


class ECMap:
    def __init__(self, **kwargs):
        """Initialize the map object."""

        init_schema = vol.Schema(
            {
                vol.Required("coordinates"): (
                    vol.All(vol.Or(int, float), vol.Range(-90, 90)),
                    vol.All(vol.Or(int, float), vol.Range(-180, 180)),
                ),
                vol.Required("radius", default=200): vol.All(int, vol.Range(min=10)),
                vol.Required("width", default=800): vol.All(int, vol.Range(min=10)),
                vol.Required("height", default=800): vol.All(int, vol.Range(min=10)),
                vol.Required("legend", default=True): bool,
                vol.Required("timestamp", default=True): bool,
                vol.Required("layer_opacity", default=65): vol.All(
                    int, vol.Range(0, 100)
                ),
                vol.Required("layer", default="rain"): vol.In(wms_layers.keys()),
                vol.Required("colors", default=14): vol.In([8, 14]),
                vol.Optional("language", default="english"): vol.In(
                    ["english", "french"]
                ),
                vol.Required("fps", default=5): vol.All(int, vol.Range(1, 30)),
                vol.Required("loop_minutes", default=0): vol.All(int, vol.Range(min=0)),
                vol.Required("interpolation", default=False): bool,
                vol.Required("webp", default=False): bool,
                vol.Required("future_minutes", default=0): vol.All(
                    int, vol.Range(min=0)
                ),
            }
        )

        kwargs = init_schema(kwargs)
        self.language = kwargs["language"]
        self.metadata = {"attribution": ATTRIBUTION[self.language]}

        # Get layer
        self.layer = kwargs["layer"]
        self.colors = kwargs["colors"]

        # Get map parameters
        self.image = None
        self.width = kwargs["width"]
        self.height = kwargs["height"]
        self.bbox = _compute_bounding_box(kwargs["radius"], *kwargs["coordinates"])
        self.map_params = {
            "bbox": ",".join([str(coord) for coord in self.bbox]),
            "width": self.width,
            "height": self.height,
        }
        self.layer_opacity = kwargs["layer_opacity"]

        # Get overlay parameters
        self.show_legend = kwargs["legend"]
        self.show_timestamp = kwargs["timestamp"]

        # Get animation parameters
        self.fps = kwargs["fps"]
        # 0 means use the full range of images the WMS server reports
        self.loop_minutes = kwargs["loop_minutes"]
        self.webp = kwargs["webp"]

        # Smooths the WMS-rendered radar layer instead of leaving it pixelated
        self.interpolation = kwargs["interpolation"]

        # How far past "now" to extend get_loop() using the radar
        # extrapolation (nowcast) layer, if one exists for self.layer.
        self.future_minutes = kwargs["future_minutes"]
        self._future_layer = wms_layers_extrapolation.get(self.layer)
        self._future_boundary = None
        self._reference_time = None

        self.timestamp = None

    def _get_cache_prefix(self):
        """Generate a location-specific cache prefix based on bounding box."""
        return f"{self.bbox[0]:.3f},{self.bbox[1]:.3f},{self.bbox[2]:.3f},{self.bbox[3]:.3f}"

    def clear_cache(self) -> int:
        """Clear all cached data for this map location.

        This clears:
        - Basemap image
        - Layer images
        - Legend images
        - Composite images
        - Capabilities data for the current layer

        Returns:
            Number of cache entries cleared.
        """
        prefix = self._get_cache_prefix()
        count = Cache.clear(prefix)
        count += Cache.clear(f"capabilities-{wms_layers[self.layer]}")
        if self._future_layer:
            count += Cache.clear(f"capabilities-{self._future_layer}")
        return count

    async def _get_basemap(self):
        """Fetch the background map image."""
        basemap_cache_key = f"{self._get_cache_prefix()}-basemap"
        if base_bytes := Cache.get(basemap_cache_key):
            return base_bytes

        basemap_params.update(self.map_params)
        try:
            base_bytes = await _get_resource(basemap_url, basemap_params)
            return Cache.add(basemap_cache_key, base_bytes, timedelta(days=7))
        except ClientConnectorError as e:
            LOG.warning("Map from %s could not be retrieved: %s", basemap_url, e)
            return None

    def _generate_legend(self) -> Image.Image | None:
        """Generate a horizontal legend image for the current layer."""
        try:
            return generate_legend(self.layer, self.language, self.width, self.colors)
        except ValueError:
            return None

    async def _get_layer_dimension(self, layer_name, dimension="time"):
        """Fetch a WMS layer's dimension range (and default value) from
        GetCapabilities. Returns (start, end, default) or None if the layer
        or dimension doesn't exist."""

        capabilities_cache_key = f"capabilities-{layer_name}"

        if not (capabilities_xml := Cache.get(capabilities_cache_key)):
            params = {**capabilities_params, "layer": layer_name}
            capabilities_xml = await _get_resource(geomet_url, params, bytes=True)
            Cache.add(capabilities_cache_key, capabilities_xml, timedelta(minutes=5))

        element = et.fromstring(capabilities_xml).find(
            dimension_xpath.format(layer=layer_name, dim=dimension),
            namespaces=wms_namespace,
        )
        if element is None or not element.text:
            return None

        start, end = (dateutil.parser.isoparse(t) for t in element.text.split("/")[:2])
        return start, end, element.get("default")

    async def _get_dimensions(self):
        """Get the time range of currently observed images for the layer.

        Resets any future-extension state from a previous get_loop() call,
        so get_latest_frame() always returns a real observation rather than
        a leftover forecast frame.
        """
        self._future_boundary = None
        self._reference_time = None

        result = await self._get_layer_dimension(wms_layers[self.layer])
        if result is None:
            return None
        start, end, _ = result
        self.timestamp = end.isoformat()
        return (start, end)

    async def _extend_into_future(self, end):
        """Extend `end` using the radar extrapolation layer, if
        future_minutes is set and one exists for self.layer. Pins
        self._future_boundary/_reference_time for _resolve_layer to use
        while building the frames for this loop."""

        if not (self.future_minutes and self._future_layer):
            return end

        future = await self._get_layer_dimension(self._future_layer, "time")
        if not future:
            LOG.warning(
                "Extrapolation layer %s has no data; future_minutes ignored",
                self._future_layer,
            )
            return end
        future_start, future_end, _ = future

        # Pin all forecast frames to a single model run so the loop doesn't
        # jitter between runs as "now" advances mid-build.
        reference = await self._get_layer_dimension(
            self._future_layer, "reference_time"
        )
        self._future_boundary = future_start
        self._reference_time = reference[2] if reference else None

        return min(future_end, end + timedelta(minutes=self.future_minutes))

    def _resolve_layer(self, frame_time):
        """Return (wms_layer_name, is_future) for a given frame time."""
        if self._future_boundary is not None and frame_time >= self._future_boundary:
            return self._future_layer, True
        return wms_layers[self.layer], False

    async def _get_layer_image(self, frame_time):
        """Fetch image for the layer at a specific time."""
        layer_name, is_future = self._resolve_layer(frame_time)
        time = frame_time.strftime("%Y-%m-%dT%H:%M:00Z")
        layer_cache_key = (
            f"{self._get_cache_prefix()}-layer-{layer_name}-{self.colors}"
            f"-{self.interpolation}-{self.webp}-{time}"
        )

        if img := Cache.get(layer_cache_key):
            return img

        params = dict(
            **map_params,
            **self.map_params,
            layers=layer_name,
            time=time,
        )
        if style_prefix := wms_style_prefixes.get(self.layer):
            params["styles"] = f"{style_prefix}_{self.colors}colors"
        if is_future and self._reference_time:
            params["dim_reference_time"] = self._reference_time
        if self.interpolation:
            params["interpolation"] = "true"
        if self.webp:
            params["format"] = "image/webp"

        try:
            layer_bytes = await _get_resource(geomet_url, params)
            return Cache.add(layer_cache_key, layer_bytes, timedelta(minutes=200))
        except ClientConnectorError:
            LOG.warning("Layer could not be retrieved")
            return None

    async def _create_composite_image(self, frame_time):
        """Create a composite image from the layer."""

        layer_name, _ = self._resolve_layer(frame_time)
        time = frame_time.strftime("%Y-%m-%dT%H:%M:00Z")
        cache_key = (
            f"{self._get_cache_prefix()}-composite-{layer_name}-{self.colors}"
            f"-{self.interpolation}-{self.webp}-{self.language}-{time}"
        )

        if img := Cache.get(cache_key):
            return img

        def _create_image():
            """Contains all the PIL calls; run in another thread."""

            # Start with the basemap if available
            if base_bytes:
                composite = Image.open(BytesIO(base_bytes)).convert("RGBA")
            else:
                # Create a blank image if no basemap
                composite = Image.new(
                    "RGBA", (self.width, self.height), (255, 255, 255, 255)
                )

            # Add the layer with transparency
            if layer_bytes:
                layer_image = Image.open(BytesIO(layer_bytes)).convert("RGBA")

                # Add transparency to layer
                if self.layer_opacity < 100:
                    alpha = round((self.layer_opacity / 100) * 255)
                    layer_copy = layer_image.copy()
                    layer_copy.putalpha(alpha)
                    layer_image.paste(layer_copy, layer_image)

                # Composite the layer onto the image
                composite = Image.alpha_composite(composite, layer_image)

            # Add legend (bottom-centre, solid white background)
            if legend_image:
                lw, lh = legend_image.size
                lx = (self.width - lw) // 2
                ly = self.height - lh
                composite.paste(legend_image, (lx, ly))

            # Add timestamp (top-left)
            if self.show_timestamp:
                layer_text = timestamp_label.get(self.layer, {}).get(
                    self.language, self.layer
                )
                ts_text = f"{layer_text} @ {frame_time.astimezone().strftime('%H:%M')}"
                font = load_font(42)
                bbox = font.getbbox(ts_text)
                pad = 8
                # Size from draw origin (0,pad) to bottom of descenders + pad
                box_w = bbox[2] - bbox[0] + pad * 2
                box_h = bbox[3] + pad * 2
                text_box = Image.new("RGBA", (box_w, box_h), (255, 255, 255, 220))
                box_draw = ImageDraw.Draw(text_box)
                box_draw.text((pad - bbox[0], pad), ts_text, fill=(0, 0, 0), font=font)
                composite.alpha_composite(text_box, (4, 4))

            # Convert frame to PNG (or WebP, if enabled) for return
            img_byte_arr = BytesIO()
            composite.save(img_byte_arr, format="WEBP" if self.webp else "PNG")

            return Cache.add(
                cache_key,
                img_byte_arr.getvalue(),
                timedelta(minutes=200),
            )

        base_bytes = await self._get_basemap()
        layer_bytes = await self._get_layer_image(frame_time)
        legend_image = self._generate_legend() if self.show_legend else None

        return await asyncio.get_event_loop().run_in_executor(None, _create_image)

    async def get_latest_frame(self):
        """Get the latest image with the specified layer."""
        dimensions = await self._get_dimensions()
        if not dimensions:
            return None

        return await self._create_composite_image(frame_time=dimensions[1])

    async def update(self):
        self.image = await self.get_loop()

    async def get_loop(self, fps=None):
        """Build an animated GIF (or WebP, if enabled) of recent images with the specified layer."""

        if fps is None:
            fps = self.fps

        def create_animation():
            """Assemble animated GIF or WebP."""
            duration = 1000 / fps
            imgs = [
                Image.open(BytesIO(img)).convert("RGBA") for img in composite_frames
            ]
            animation = BytesIO()
            imgs[0].save(
                animation,
                format="WEBP" if self.webp else "GIF",
                save_all=True,
                append_images=imgs[1:],
                duration=duration,
                loop=0,
            )
            return animation.getvalue()

        await self._get_basemap()

        # Use the layer to determine the time dimensions
        timespan = await self._get_dimensions()
        if not timespan:
            LOG.error("Cannot retrieve image times.")
            return None

        start, now = timespan
        if self.loop_minutes:
            start = max(start, now - timedelta(minutes=self.loop_minutes))

        # Extend the end of the loop using the extrapolation (nowcast) layer,
        # if future_minutes is set and one exists for self.layer. Anchored to
        # `now`, not the (possibly loop_minutes-truncated) `start`, so a short
        # loop_minutes doesn't eat into the forward-looking portion.
        end = await self._extend_into_future(now)

        tasks = []
        curr = start
        while curr <= end:
            tasks.append(self._create_composite_image(frame_time=curr))
            curr = curr + image_interval
        composite_frames = await asyncio.gather(*tasks)

        # Repeat the last frame 3 times to make it pause at the end
        for _ in range(3):
            composite_frames.append(composite_frames[-1])

        return await asyncio.get_running_loop().run_in_executor(None, create_animation)
