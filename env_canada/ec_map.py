import asyncio
import logging
import math
import os
from datetime import date, timedelta
from io import BytesIO
from typing import cast, Dict, List, Optional, Tuple, Union

import dateutil.parser
import voluptuous as vol
from aiohttp import ClientSession
from aiohttp.client_exceptions import ClientConnectorError
from lxml import etree as et
from PIL import Image, ImageDraw, ImageFont

from .constants import USER_AGENT
from .ec_cache import Cache

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

# Mapbox Proxy

backup_map_url = (
    "https://0wmiyoko9f.execute-api.ca-central-1.amazonaws.com/mapbox-proxy"
)

# Environment Canada

# Common WMS layers available from Environment Canada
wms_layers = {"rain": "RADAR_1KM_RRAI", "snow": "RADAR_1KM_RSNO"}

legend_style = {"rain": "RADARURPPRECIPR", "snow": "RADARURPPRECIPS14"}

geomet_url = "https://geo.weather.gc.ca/geomet"
capabilities_params = {
    "lang": "en",
    "service": "WMS",
    "version": "1.3.0",
    "request": "GetCapabilities",
}
wms_namespace = {"wms": "http://www.opengis.net/wms"}
dimension_xpath = './/wms:Layer[wms:Name="{layer}"]/wms:Dimension'
map_params = {
    "service": "WMS",
    "version": "1.3.0",
    "request": "GetMap",
    "crs": "EPSG:4326",
    "format": "image/png",
}
legend_params = {
    "service": "WMS",
    "version": "1.3.0",
    "request": "GetLegendGraphic",
    "sld_version": "1.1.0",
    "format": "image/png",
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

    lat_min = latittude - angular_distance
    lat_max = latittude + angular_distance

    delta_longitude = math.asin(math.sin(angular_distance) / math.cos(latittude))

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
                vol.Required("layers", default=["rain"]): vol.All(
                    vol.Any(list, tuple),
                    vol.Length(min=1),
                    vol.Schema([vol.In(wms_layers.keys())]),
                ),
                vol.Optional("language", default="english"): vol.In(
                    ["english", "french"]
                ),
            }
        )

        kwargs = init_schema(kwargs)
        self.language = kwargs["language"]
        self.metadata = {"attribution": ATTRIBUTION[self.language]}

        # Get layers
        self.layers = kwargs["layers"]

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

        self._font = None
        self.timestamp = None

    async def _get_basemap(self):
        """Fetch the background map image."""
        if base_bytes := Cache.get("basemap"):
            return base_bytes

        basemap_params.update(self.map_params)
        for map_url in [basemap_url, backup_map_url]:
            try:
                base_bytes = await _get_resource(map_url, basemap_params)
                return Cache.add("basemap", base_bytes, timedelta(days=7))

            except ClientConnectorError as e:
                logging.warning("Map from %s could not be retrieved: %s", map_url, e)

    async def _get_legend(self, layer):
        """Fetch legend image for a specific layer."""

        legend_cache_key = f"legend-{layer}"
        if legend := Cache.get(legend_cache_key):
            return legend

        legend_params.update(
            dict(
                layer=wms_layers[layer],
                style=legend_style[layer],
            )
        )
        try:
            legend = await _get_resource(geomet_url, legend_params)
            return Cache.add(legend_cache_key, legend, timedelta(days=7))

        except ClientConnectorError:
            logging.warning(f"Legend for {layer} could not be retrieved")
            return None

    async def _get_dimensions(self, layer):
        """Get time range of available images for a specific layer."""

        capabilities_cache_key = f"capabilities-{layer}"

        if not (capabilities_xml := Cache.get(capabilities_cache_key)):
            capabilities_params["layer"] = wms_layers[layer]
            capabilities_xml = await _get_resource(
                geomet_url, capabilities_params, bytes=True
            )
            Cache.add(capabilities_cache_key, capabilities_xml, timedelta(minutes=5))

        dimension_string = et.fromstring(capabilities_xml).find(
            dimension_xpath.format(layer=wms_layers[layer]),
            namespaces=wms_namespace,
        )
        if dimension_string is not None:
            if dimension_string := dimension_string.text:
                start, end = (
                    dateutil.parser.isoparse(t) for t in dimension_string.split("/")[:2]
                )
                self.timestamp = end.isoformat()
                return (start, end)
        return None

    async def _get_layer_image(self, layer, frame_time):
        """Fetch image for a specific layer at a specific time."""
        time = frame_time.strftime("%Y-%m-%dT%H:%M:00Z")
        layer_cache_key = f"layer-{layer}-{time}"

        if img := Cache.get(layer_cache_key):
            return img

        params = dict(
            **map_params,
            **self.map_params,
            layers=wms_layers[layer],
            time=time,
        )

        try:
            layer_bytes = await _get_resource(geomet_url, params)
            return Cache.add(layer_cache_key, layer_bytes, timedelta(minutes=200))
        except ClientConnectorError:
            logging.warning(f"Layer {layer} could not be retrieved")
            return None

    async def _create_composite_image(self, frame_time):
        """Create a composite image from multiple layers."""

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

            # Add each layer with transparency
            for layer_name, layer_bytes in layer_images.items():
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

            # Add legends
            legend_y_offset = 0
            for layer_name, legend_bytes in legend_images.items():
                if legend_bytes:
                    legend_image = Image.open(BytesIO(legend_bytes)).convert("RGB")
                    legend_position = (
                        self.width - legend_image.size[0],
                        legend_y_offset,
                    )
                    composite.paste(legend_image, legend_position)
                    legend_y_offset += legend_image.size[1]

            # Add timestamp
            if self.show_timestamp:
                if not self._font:
                    self._font = ImageFont.load(
                        os.path.join(os.path.dirname(__file__), "10x20.pil")
                    )

                if self._font:
                    # Create a timestamp with all active layers
                    layer_labels = []
                    for layer in self.layers:
                        if layer in timestamp_label:
                            layer_labels.append(timestamp_label[layer][self.language])

                    layers_text = ", ".join(layer_labels)
                    timestamp = (
                        f"{layers_text} @ {frame_time.astimezone().strftime('%H:%M')}"
                    )

                    text_box = Image.new(
                        "RGBA", self._font.getbbox(timestamp)[2:], "white"
                    )
                    box_draw = ImageDraw.Draw(text_box)
                    box_draw.text(
                        xy=(0, 0), text=timestamp, fill=(0, 0, 0), font=self._font
                    )
                    double_box = text_box.resize(
                        (text_box.width * 2, text_box.height * 2)
                    )
                    composite.paste(double_box)
                    composite = composite.quantize()

            # Convert frame to PNG for return
            img_byte_arr = BytesIO()
            composite.save(img_byte_arr, format="PNG")

            return Cache.add(
                f"composite-{time}", img_byte_arr.getvalue(), timedelta(minutes=200)
            )

        time = frame_time.strftime("%Y-%m-%dT%H:%M:00Z")
        cache_key = f"composite-{time}"

        if img := Cache.get(cache_key):
            return img

        # Get the basemap
        base_bytes = await self._get_basemap()

        # Get all layer images
        layer_images = {}
        legend_images = {}

        for layer in self.layers:
            layer_images[layer] = await self._get_layer_image(layer, frame_time)
            if self.show_legend:
                legend_images[layer] = await self._get_legend(layer)

        return await asyncio.get_event_loop().run_in_executor(None, _create_image)

    async def get_latest_frame(self):
        """Get the latest image with all specified layers."""
        # Use the first layer to determine the time dimensions
        if not self.layers:
            return None

        primary_layer = self.layers[0]
        dimensions = await self._get_dimensions(primary_layer)
        if not dimensions:
            return None

        return await self._create_composite_image(frame_time=dimensions[1])

    async def update(self):
        self.image = await self.get_loop()

    async def get_loop(self, fps=5):
        """Build an animated GIF of recent images with all specified layers."""

        def create_gif():
            """Assemble animated GIF."""
            duration = 1000 / fps
            imgs = [
                Image.open(BytesIO(img)).convert("RGBA") for img in composite_frames
            ]
            gif = BytesIO()
            imgs[0].save(
                gif,
                format="GIF",
                save_all=True,
                append_images=imgs[1:],
                duration=duration,
                loop=0,
            )
            return gif.getvalue()

        # Without this cache priming the tasks below each compete to load map/legend
        # at the same time, resulting in them getting retrieved for each image.
        await self._get_basemap()
        for layer in self.layers:
            if self.show_legend:
                await self._get_legend(layer)

        # Use the first layer to determine the time dimensions
        primary_layer = self.layers[0]
        timespan = await self._get_dimensions(primary_layer)
        if not timespan:
            logging.error("Cannot retrieve image times.")
            return None

        tasks = []
        curr = timespan[0]
        while curr <= timespan[1]:
            tasks.append(self._create_composite_image(frame_time=curr))
            curr = curr + image_interval
        composite_frames = await asyncio.gather(*tasks)

        # Repeat the last frame 3 times to make it pause at the end
        for _ in range(3):
            composite_frames.append(composite_frames[-1])

        return await asyncio.get_running_loop().run_in_executor(None, create_gif)
