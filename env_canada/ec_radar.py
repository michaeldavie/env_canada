from concurrent.futures import as_completed
import datetime
from io import BytesIO
import json
import os
from PIL import Image, ImageDraw, ImageFont
import xml.etree.ElementTree as et

import dateutil.parser
import imageio
import numpy as np
import requests
from requests_futures.sessions import FuturesSession

# Natural Resources Canada

basemap_url = "http://maps.geogratis.gc.ca/wms/CBMT?service=wms&version=1.3.0&request=GetMap&layers=CBMT&styles=&CRS=epsg:4326&BBOX={south},{west},{north},{east}&width={width}&height={height}&format=image/png"

# Environment Canada

layer = {"rain": "RADAR_1KM_RRAI", "snow": "RADAR_1KM_RSNO"}

legend_style = {"rain": "RADARURPPRECIPR", "snow": "RADARURPPRECIPS14"}

capabilities_path = "https://geo.weather.gc.ca/geomet/?lang=en&service=WMS&version=1.3.0&request=GetCapabilities&LAYER={layer}"
wms_namespace = {"wms": "http://www.opengis.net/wms"}
dimension_xpath = './/wms:Layer[wms:Name="{layer}"]/wms:Dimension'

radar_path = "https://geo.weather.gc.ca/geomet?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&BBOX={south},{west},{north},{east}&CRS=EPSG:4326&WIDTH={width}&HEIGHT={height}&LAYERS={layer}&FORMAT=image/png&TIME={time}"
legend_path = "https://geo.weather.gc.ca/geomet?version=1.3.0&service=WMS&request=GetLegendGraphic&sld_version=1.1.0&layer={layer}&format=image/png&STYLE={style}"


def get_station_coords(station_id):
    with open(
        os.path.join(os.path.dirname(__file__), "radar_sites.json")
    ) as sites_file:
        site_dict = json.loads(sites_file.read())
    return site_dict[station_id]["lat"], site_dict[station_id]["lon"]


def get_bounding_box(distance, latittude, longitude):
    """
    Modified from https://gist.github.com/alexcpn/f95ae83a7ee0293a5225
    """
    latittude = np.radians(latittude)
    longitude = np.radians(longitude)

    distance_from_point_km = distance
    angular_distance = distance_from_point_km / 6371.01

    lat_min = latittude - angular_distance
    lat_max = latittude + angular_distance

    delta_longitude = np.arcsin(np.sin(angular_distance) / np.cos(latittude))

    lon_min = longitude - delta_longitude
    lon_max = longitude + delta_longitude
    lon_min = np.degrees(lon_min)
    lat_max = np.degrees(lat_max)
    lon_max = np.degrees(lon_max)
    lat_min = np.degrees(lat_min)

    return lat_min, lon_min, lat_max, lon_max


class ECRadar(object):
    def __init__(
        self,
        station_id=None,
        coordinates=None,
        radius=200,
        precip_type=None,
        width=800,
        height=800,
    ):
        """Initialize the data object."""

        # Set precipitation type

        if precip_type:
            self.precip_type = precip_type.lower()
        elif datetime.date.today().month in range(4, 11):
            self.precip_type = "rain"
        else:
            self.precip_type = "snow"

        self.layer = layer[self.precip_type]

        # Get legend

        legend_url = legend_path.format(
            layer=self.layer, style=legend_style[self.precip_type]
        )
        legend_bytes = requests.get(url=legend_url).content
        self.legend_image = Image.open(BytesIO(legend_bytes)).convert("RGB")
        legend_width, legend_height = self.legend_image.size
        self.legend_position = (width - legend_width, 0)

        # Get coordinates

        if station_id:
            coordinates = get_station_coords(station_id)

        self.bbox = get_bounding_box(radius, coordinates[0], coordinates[1])
        self.width = width
        self.height = height

        # Get basemap

        url = basemap_url.format(
            south=self.bbox[0],
            west=self.bbox[1],
            north=self.bbox[2],
            east=self.bbox[3],
            width=self.width,
            height=self.height,
        )
        self.base_bytes = requests.get(url).content

        self.timestamp = datetime.datetime.now()

    def get_dimensions(self):
        """Get time range of available data."""
        capabilities_xml = requests.get(capabilities_path.format(layer=self.layer)).text
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

    def assemble_url(self, url_time):
        """Construct WMS query URL."""
        url = radar_path.format(
            south=self.bbox[0],
            west=self.bbox[1],
            north=self.bbox[2],
            east=self.bbox[3],
            width=self.width,
            height=self.height,
            layer=self.layer,
            time=url_time.strftime("%Y-%m-%dT%H:%M:00Z"),
        )
        return url

    def combine_layers(self, radar_bytes, frame_time):
        """Add radar overlay to base layer and add timestamp."""

        # Overlay radar on basemap

        base = Image.open(BytesIO(self.base_bytes)).convert("RGBA")
        radar = Image.open(BytesIO(radar_bytes)).convert("RGBA")
        frame = Image.alpha_composite(base, radar)
        frame.paste(self.legend_image, self.legend_position)

        # Add timestamp

        timestamp = (
            self.precip_type.title() + " @ " + frame_time.astimezone().strftime("%H:%M")
        )
        font = ImageFont.load(os.path.join(os.path.dirname(__file__), "10x20.pil"))
        text_box = Image.new("RGBA", font.getsize(timestamp), "white")

        box_draw = ImageDraw.Draw(text_box)
        box_draw.text(xy=(0, 0), text=timestamp, fill=(0, 0, 0), font=font)
        double_box = text_box.resize((text_box.width * 2, text_box.height * 2))

        frame.paste(double_box)

        # Return frame as PNG bytes

        img_byte_arr = BytesIO()
        frame.save(img_byte_arr, format="PNG")
        frame_bytes = img_byte_arr.getvalue()

        return frame_bytes

    def get_latest_frame(self):
        """Get the latest image from Environment Canada."""
        start, end = self.get_dimensions()
        radar = requests.get(self.assemble_url(end)).content
        return self.combine_layers(radar, end)

    def get_loop(self):
        """Build an animated GIF of recent radar images."""

        """Build list of frame timestamps."""
        start, end = self.get_dimensions()
        frame_times = [start]

        while True:
            next_frame = frame_times[-1] + datetime.timedelta(minutes=10)
            if next_frame > end:
                break
            else:
                frame_times.append(next_frame)

        """Fetch frames."""
        responses = []

        with FuturesSession(max_workers=len(frame_times)) as session:
            futures = [session.get(self.assemble_url(t)) for t in frame_times]
            for future in as_completed(futures):
                responses.append(future.result())

        responses = sorted(responses, key=lambda r: r.url)

        frames = []

        for i, f in enumerate(responses):
            frames.append(self.combine_layers(f.content, frame_times[i]))

        for f in range(3):
            frames.append(frames[-1])

        """Assemble animated GIF."""
        gif_frames = [imageio.imread(f) for f in frames]
        gif_bytes = imageio.mimwrite(
            imageio.RETURN_BYTES, gif_frames, format="GIF", fps=5
        )
        return gif_bytes
