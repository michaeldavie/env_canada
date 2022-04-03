from aiohttp.client_exceptions import ClientConnectorError
import asyncio
import datetime
from io import BytesIO
import logging
import math
import os
from PIL import Image, ImageDraw, ImageFont
import xml.etree.ElementTree as et

from .ec_cache import CacheClientSession as ClientSession
import dateutil.parser
import imageio
import voluptuous as vol

ATTRIBUTION = {
    "english": "Data provided by Environment Canada",
    "french": "Données fournies par Environnement Canada",
}

# Natural Resources Canada

basemap_url = "http://maps.geogratis.gc.ca/wms/CBMT"
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

timestamp_label = {
    "rain": {"english": "Rain", "french": "Pluie"},
    "snow": {"english": "Snow", "french": "Neige"},
}


def compute_bounding_box(distance, latittude, longitude):
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
        self.bbox = compute_bounding_box(kwargs["radius"], *kwargs["coordinates"])
        self.map_params = {
            "bbox": ",".join([str(coord) for coord in self.bbox]),
            "width": self.width,
            "height": self.height,
        }
        self.map_image = None
        self.radar_opacity = kwargs["radar_opacity"]

        # Get overlay parameters

        self.show_legend = kwargs["legend"]
        if self.show_legend:
            self.legend_layer = None
            self.legend_image = None
            self.legend_position = None

        self.show_timestamp = kwargs["timestamp"]
        if self.show_timestamp:
            self.font = ImageFont.load(
                os.path.join(os.path.dirname(__file__), "10x20.pil")
            )

    @property
    def precip_type(self):
        return self._precip_setting

    @precip_type.setter
    def precip_type(self, user_input):
        if user_input not in ["rain", "snow", "auto"]:
            raise ValueError("precip_type must be 'rain', 'snow', or 'auto'")

        self._precip_setting = user_input

        if self._precip_setting in ["rain", "snow"]:
            self.layer_key = self._precip_setting
        else:
            self._auto_precip_type()

    def _auto_precip_type(self):
        if datetime.date.today().month in range(4, 11):
            self.layer_key = "rain"
        else:
            self.layer_key = "snow"

    async def _get_basemap(self):
        """Fetch the background map image."""
        basemap_params.update(self.map_params)

        try:
            async with ClientSession(raise_for_status=True) as session:
                response = await session.get(url=basemap_url, params=basemap_params)
                base_bytes = await response.read()
                self.map_image = Image.open(BytesIO(base_bytes)).convert("RGBA")

        except ClientConnectorError:
            logging.warning("NRCan base map could not be retreived")

            try:
                async with ClientSession(raise_for_status=True) as session:
                    response = await session.get(
                        url=backup_map_url, params=basemap_params
                    )
                    base_bytes = await response.read()
                    self.map_image = Image.open(BytesIO(base_bytes)).convert("RGBA")
            except ClientConnectorError:
                logging.warning("Mapbox base map could not be retreived")

        return

    async def _get_legend(self):
        """Fetch legend image."""
        legend_params.update(
            dict(
                layer=precip_layers[self.layer_key], style=legend_style[self.layer_key]
            )
        )
        async with ClientSession(raise_for_status=True) as session:
            response = await session.get(url=geomet_url, params=legend_params)
            legend_bytes = await response.read()
        self.legend_image = Image.open(BytesIO(legend_bytes)).convert("RGB")
        legend_width = self.legend_image.size[0]
        self.legend_position = (self.width - legend_width, 0)
        self.legend_layer = self.layer_key

    async def _get_dimensions(self):
        """Get time range of available data."""
        capabilities_params["layer"] = precip_layers[self.layer_key]

        async with ClientSession(raise_for_status=True) as session:
            response = await session.get(
                url=geomet_url,
                params=capabilities_params,
                cache_time=datetime.timedelta(minutes=5),
            )
            capabilities_xml = await response.text()

        capabilities_tree = et.fromstring(
            capabilities_xml, parser=et.XMLParser(encoding="utf-8")
        )
        dimension_string = capabilities_tree.find(
            dimension_xpath.format(layer=precip_layers[self.layer_key]),
            namespaces=wms_namespace,
        ).text
        start, end = [
            dateutil.parser.isoparse(t) for t in dimension_string.split("/")[:2]
        ]
        self.timestamp = end.isoformat()
        return start, end

    async def _combine_layers(self, radar_bytes, frame_time):
        """Add radar overlay to base layer and add timestamp."""

        radar = Image.open(BytesIO(radar_bytes)).convert("RGBA")

        # Add transparency to radar

        if self.radar_opacity < 100:
            alpha = round((self.radar_opacity / 100) * 255)
            radar_copy = radar.copy()
            radar_copy.putalpha(alpha)
            radar.paste(radar_copy, radar)

        # Overlay radar on basemap

        if not self.map_image:
            await self._get_basemap()
        if self.map_image:
            frame = Image.alpha_composite(self.map_image, radar)
        else:
            frame = radar

        # Add legend

        if self.show_legend:
            if not self.legend_image or self.legend_layer != self.layer_key:
                await self._get_legend()
            frame.paste(self.legend_image, self.legend_position)

        # Add timestamp

        if self.show_timestamp:
            timestamp = (
                timestamp_label[self.layer_key][self.language]
                + " @ "
                + frame_time.astimezone().strftime("%H:%M")
            )
            text_box = Image.new("RGBA", self.font.getsize(timestamp), "white")
            box_draw = ImageDraw.Draw(text_box)
            box_draw.text(xy=(0, 0), text=timestamp, fill=(0, 0, 0), font=self.font)
            double_box = text_box.resize((text_box.width * 2, text_box.height * 2))
            frame.paste(double_box)
            frame = frame.quantize()

        # Return frame as PNG bytes

        img_byte_arr = BytesIO()
        frame.save(img_byte_arr, format="PNG")
        frame_bytes = img_byte_arr.getvalue()

        return frame_bytes

    async def _get_radar_image(self, session, frame_time):
        params = dict(
            **radar_params,
            **self.map_params,
            layers=precip_layers[self.layer_key],
            time=frame_time.strftime("%Y-%m-%dT%H:%M:00Z")
        )
        response = await session.get(url=geomet_url, params=params)
        return await response.read()

    async def get_latest_frame(self):
        """Get the latest image from Environment Canada."""
        dimensions = await self._get_dimensions()
        latest = dimensions[1]
        async with ClientSession(raise_for_status=True) as session:
            frame = await self._get_radar_image(session=session, frame_time=latest)
        return await self._combine_layers(frame, latest)

    async def update(self):
        if self.precip_type == "auto":
            self._auto_precip_type()

        self.image = await self.get_loop()

    async def get_loop(self, fps=5):
        """Build an animated GIF of recent radar images."""

        """Build list of frame timestamps."""
        start, end = await self._get_dimensions()
        frame_times = [start]

        while True:
            next_frame = frame_times[-1] + datetime.timedelta(minutes=10)
            if next_frame > end:
                break
            else:
                frame_times.append(next_frame)

        """Fetch frames."""

        tasks = []
        async with ClientSession(raise_for_status=True) as session:
            for t in frame_times:
                tasks.append(self._get_radar_image(session=session, frame_time=t))
            radar_layers = await asyncio.gather(*tasks)

        frames = []

        for i, f in enumerate(radar_layers):
            frames.append(await self._combine_layers(f, frame_times[i]))

        for f in range(3):
            frames.append(frames[-1])

        """Assemble animated GIF."""
        gif_frames = [imageio.imread(f) for f in frames]
        gif_bytes = imageio.mimwrite(
            imageio.RETURN_BYTES, gif_frames, format="GIF", fps=fps, subrectangles=True
        )
        return gif_bytes
