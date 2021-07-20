import asyncio
import datetime
from io import BytesIO
import math
import os
from PIL import Image, ImageDraw, ImageFont
import xml.etree.ElementTree as et

from aiohttp import ClientSession
import dateutil.parser
import imageio

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

# Environment Canada

layer = {"rain": "RADAR_1KM_RRAI", "snow": "RADAR_1KM_RSNO"}

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
    def __init__(
        self,
        coordinates=None,
        radius=200,
        precip_type=None,
        width=800,
        height=800,
        legend=True,
        timestamp=True,
        radar_opacity=65,
    ):
        """Initialize the radar object."""

        # Set precipitation type

        if precip_type:
            self.precip_type = precip_type.lower()
        elif datetime.date.today().month in range(4, 11):
            self.precip_type = "rain"
        else:
            self.precip_type = "snow"

        self.layer = layer[self.precip_type]

        # Get map parameters

        self.bbox = compute_bounding_box(radius, coordinates[0], coordinates[1])
        self.map_params = {
            "bbox": ",".join([str(coord) for coord in self.bbox]),
            "width": width,
            "height": height,
        }

        self.width = width
        self.height = height

        self.base_bytes = None

        self.legend = legend
        if legend:
            self.legend_image = None
            self.legend_position = None

        if timestamp:
            self.font = ImageFont.load(
                os.path.join(os.path.dirname(__file__), "10x20.pil")
            )
            self.timestamp = datetime.datetime.now()

        self.radar_opacity = radar_opacity

    async def _get_basemap(self):
        """Fetch the background map image."""
        basemap_params.update(self.map_params)
        async with ClientSession() as session:
            response = await session.get(url=basemap_url, params=basemap_params)
            self.base_bytes = await response.read()

    async def _get_legend(self):
        """Fetch legend image."""
        legend_params.update(
            dict(layer=self.layer, style=legend_style[self.precip_type])
        )
        async with ClientSession() as session:
            response = await session.get(url=geomet_url, params=legend_params)
            legend_bytes = await response.read()
        self.legend_image = Image.open(BytesIO(legend_bytes)).convert("RGB")
        legend_width, legend_height = self.legend_image.size
        self.legend_position = (self.width - legend_width, 0)

    async def _get_dimensions(self):
        """Get time range of available data."""
        capabilities_params["layer"] = self.layer

        async with ClientSession() as session:
            response = await session.get(url=geomet_url, params=capabilities_params)
            capabilities_xml = await response.text()

        capabilities_tree = et.fromstring(
            capabilities_xml, parser=et.XMLParser(encoding="utf-8")
        )
        dimension_string = capabilities_tree.find(
            dimension_xpath.format(layer=self.layer), namespaces=wms_namespace
        ).text
        start, end = [
            dateutil.parser.isoparse(t) for t in dimension_string.split("/")[:2]
        ]
        self.timestamp = end.isoformat()
        return start, end

    async def _combine_layers(self, radar_bytes, frame_time):
        """Add radar overlay to base layer and add timestamp."""

        # Overlay radar on basemap

        if not self.base_bytes:
            await self._get_basemap()

        base = Image.open(BytesIO(self.base_bytes)).convert("RGBA")
        radar = Image.open(BytesIO(radar_bytes)).convert("RGBA")

        # Add transparency to radar

        if self.radar_opacity < 100:
            alpha = round((self.radar_opacity / 100) * 255)
            radar_copy = radar.copy()
            radar_copy.putalpha(alpha)
            radar.paste(radar_copy, radar)

        frame = Image.alpha_composite(base, radar)

        # Add legend

        if self.legend:
            if not self.legend_image:
                await self._get_legend()
            frame.paste(self.legend_image, self.legend_position)

        # Add timestamp

        if self.timestamp:
            timestamp = (
                self.precip_type.title()
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
            layers=self.layer,
            time=frame_time.strftime("%Y-%m-%dT%H:%M:00Z")
        )
        response = await session.get(url=geomet_url, params=params)
        return await response.read()

    async def get_latest_frame(self):
        """Get the latest image from Environment Canada."""
        dimensions = await self._get_dimensions()
        latest = dimensions[1]
        async with ClientSession() as session:
            frame = await self._get_radar_image(session=session, frame_time=latest)
        return await self._combine_layers(frame, latest)

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
        async with ClientSession() as session:
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
