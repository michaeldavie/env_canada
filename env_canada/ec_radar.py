import asyncio
from datetime import date, timedelta
import logging
import math
import os
from io import BytesIO

import dateutil.parser
import defusedxml.ElementTree as et
import imageio.v2 as imageio
import voluptuous as vol
from aiohttp import ClientSession
from aiohttp.client_exceptions import ClientConnectorError
from PIL import Image, ImageDraw, ImageFont

from .constants import USER_AGENT
from .ec_cache import Cache

ATTRIBUTION = {
    "english": "Data provided by Environment Canada",
    "french": "Données fournies par Environnement Canada",
}

__all__ = ["ECRadar"]

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

precip_layers = {"rain": "RADAR_1KM_RRAI", "snow": "RADAR_1KM_RSNO"}

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
radar_params = {
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
radar_interval = timedelta(minutes=6)

timestamp_label = {
    "rain": {"english": "Rain", "french": "Pluie"},
    "snow": {"english": "Snow", "french": "Neige"},
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


class ECRadar(object):
    def __init__(self, **kwargs):
        """Initialize the radar object."""

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
                vol.Required("radar_opacity", default=65): vol.All(
                    int, vol.Range(0, 100)
                ),
                vol.Optional("precip_type"): vol.Any(
                    None, vol.In(["rain", "snow", "auto"])
                ),
                vol.Optional("language", default="english"): vol.In(
                    ["english", "french"]
                ),
            }
        )

        kwargs = init_schema(kwargs)
        self.language = kwargs["language"]
        self.metadata = {"attribution": ATTRIBUTION[self.language]}

        # Set precipitation type

        if "precip_type" in kwargs and kwargs["precip_type"] is not None:
            self.precip_type = kwargs["precip_type"]
        else:
            self.precip_type = "auto"

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
        self.radar_opacity = kwargs["radar_opacity"]

        # Get overlay parameters

        self.show_legend = kwargs["legend"]
        self.show_timestamp = kwargs["timestamp"]

        self._font = None
        self._cached_layer_key = None

    @property
    def precip_type(self):
        return self.layer_key

    @precip_type.setter
    def precip_type(self, user_input):
        if user_input not in ["rain", "snow", "auto"]:
            raise ValueError("precip_type must be 'rain', 'snow', or 'auto'")

        if user_input == "auto":
            self._auto_precip_type()
        else:
            self.layer_key = user_input

    def _auto_precip_type(self):
        self.layer_key = "rain" if date.today().month in range(4, 11) else "snow"

    async def _get_basemap(self):
        """Fetch the background map image."""
        if base_bytes := Cache.get("basemap"):
            return base_bytes

        basemap_params.update(self.map_params)
        for map_url in [basemap_url, backup_map_url]:
            try:
                base_bytes = await _get_resource(map_url, basemap_params)
                return Cache.add("basemap", base_bytes)

            except ClientConnectorError as e:
                logging.warning("Map from %s could not be retrieved: %s" % map_url, e)

    async def _get_legend(self):
        """Fetch legend image."""

        if self._cached_layer_key == self.layer_key:
            if legend := Cache.get("legend"):
                return legend

        legend_params.update(
            dict(
                layer=precip_layers[self.layer_key], style=legend_style[self.layer_key]
            )
        )
        try:
            legend = await _get_resource(geomet_url, legend_params)
            self._cached_layer_key = self.layer_key
            return Cache.add("legend", legend)

        except ClientConnectorError:
            logging.warning("Legend could not be retrieved")
            return None

    async def _get_dimensions(self):
        """Get time range of available data."""
        if not (capabilities_xml := Cache.get("capabilities")):
            capabilities_params["layer"] = precip_layers[self.layer_key]
            capabilities_xml = await _get_resource(
                geomet_url, capabilities_params, bytes=False
            )
            Cache.add("capabilities", capabilities_xml, cache_time=timedelta(minutes=5))

        dimension_string = et.fromstring(capabilities_xml).find(
            dimension_xpath.format(layer=precip_layers[self.layer_key]),
            namespaces=wms_namespace,
        )
        if dimension_string is not None:
            if dimension_string := dimension_string.text:
                start, end = [
                    dateutil.parser.isoparse(t) for t in dimension_string.split("/")[:2]
                ]
                self.timestamp = end.isoformat()
                return (start, end)
        return None

    async def _get_radar_image(self, frame_time):
        # All the synchronous PIL stuff here
        def _create_image():
            radar_image = Image.open(BytesIO(radar_bytes)).convert("RGBA")

            map_image = None
            if base_bytes:
                map_image = Image.open(BytesIO(base_bytes)).convert("RGBA")

            if legend_bytes:
                legend_image = Image.open(BytesIO(legend_bytes)).convert("RGB")
                legend_position = (self.width - legend_image.size[0], 0)
            else:
                legend_image = None
                legend_position = None

            # Add transparency to radar
            if self.radar_opacity < 100:
                alpha = round((self.radar_opacity / 100) * 255)
                radar_copy = radar_image.copy()
                radar_copy.putalpha(alpha)
                radar_image.paste(radar_copy, radar_image)

            if self.show_timestamp and not self._font:
                self._font = ImageFont.load(
                    os.path.join(os.path.dirname(__file__), "10x20.pil")
                )

            # Overlay radar on basemap
            if map_image:
                frame = Image.alpha_composite(map_image, radar_image)
            else:
                frame = radar_image

            # Add legend
            if legend_image:
                frame.paste(legend_image, legend_position)

            # Add timestamp
            if self.show_timestamp:
                if not self._font:
                    self._font = ImageFont.load(
                        os.path.join(os.path.dirname(__file__), "10x20.pil")
                    )

                if self._font:
                    timestamp = f"{timestamp_label[self.layer_key][self.language]} @ {frame_time.astimezone().strftime('%H:%M')}"
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
                    frame.paste(double_box)
                    frame = frame.quantize()

            # Convert frame to PNG for return
            img_byte_arr = BytesIO()
            frame.save(img_byte_arr, format="PNG")

            return Cache.add(f"radar-{time}", img_byte_arr.getvalue())

        time = frame_time.strftime("%Y-%m-%dT%H:%M:00Z")

        if img := Cache.get(f"radar-{time}"):
            return img

        base_bytes = await self._get_basemap()
        legend_bytes = await self._get_legend() if self.show_legend else None

        params = dict(
            **radar_params,
            **self.map_params,
            layers=precip_layers[self.layer_key],
            time=time,
        )
        radar_bytes = await _get_resource(geomet_url, params)

        # Since PIL is synchronous, run all PIL stuff in another thread
        return await asyncio.get_event_loop().run_in_executor(None, _create_image)

    async def get_latest_frame(self):
        """Get the latest image from Environment Canada."""
        dimensions = await self._get_dimensions()
        if not dimensions:
            return None
        latest = dimensions[1]
        return await self._get_radar_image(frame_time=latest)

    async def update(self):
        if self.precip_type == "auto":
            self._auto_precip_type()

        self.image = await self.get_loop()

    async def get_loop(self, fps=5):
        """Build an animated GIF of recent radar images."""

        def create_gif():
            """Assemble animated GIF."""
            duration = 1000 / fps
            gif_frames = [imageio.imread(f, mode="RGBA") for f in radar_layers]
            gif_bytes = imageio.mimwrite(
                imageio.RETURN_BYTES,
                gif_frames,
                format="GIF",
                duration=duration,
                subrectangles=True,
            )
            return gif_bytes

        # Prime the cache - without this the tasks below each compete
        # to load map/legend at the same time.
        await self._get_basemap()
        await self._get_legend() if self.show_legend else None

        """Build list of frame timestamps."""
        timespan = await self._get_dimensions()
        if not timespan:
            logging.error("Cannot get capabilities")
            return None

        tasks = []
        curr = timespan[0]
        while curr <= timespan[1]:
            tasks.append(self._get_radar_image(frame_time=curr))
            curr = curr + radar_interval
        radar_layers = await asyncio.gather(*tasks)

        for _ in range(3):
            radar_layers.append(radar_layers[-1])

        return await asyncio.get_running_loop().run_in_executor(None, create_gif)
