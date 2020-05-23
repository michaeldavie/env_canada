from concurrent.futures import as_completed
import datetime
from io import BytesIO
import json
import os
from PIL import Image
import xml.etree.ElementTree as et

import dateutil.parser
import imageio
import numpy as np
import requests
from requests_futures.sessions import FuturesSession

# Natural Resources Canada

basemap_url = 'https://maps.geogratis.gc.ca/wms/CBMT?service=wms&version=1.3.0&request=GetMap&layers=CBMT&styles=&CRS=epsg:4326&BBOX={south},{west},{north},{east}&width={width}&height={height}&format=image/png'

# Environment Canada

layer = {
    'rain': 'RADAR_1KM_RRAI',
    'snow': 'RADAR_1KM_RSNO'
}

capabilities_url = 'https://geo.weather.gc.ca/geomet/?lang=en&service=WMS&version=1.3.0&request=GetCapabilities&LAYER={layer}'
wms_namespace = {'wms': 'http://www.opengis.net/wms'}
dimension_xpath = './/wms:Layer[wms:Name="{layer}"]/wms:Dimension'

radar_url = 'https://geo.weather.gc.ca/geomet?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&BBOX={south},{west},{north},{east}&CRS=EPSG:4326&WIDTH={width}&HEIGHT={height}&LAYERS={layer}&FORMAT=image/png&TIME={time}'


def get_station_coords(station_id):
    with open(os.path.join(os.path.dirname(__file__), 'radar_sites.json')) as sites_file:
        site_dict = json.loads(sites_file.read())
    return site_dict[station_id]['lat'], site_dict[station_id]['lon']


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
    def __init__(self, station_id=None, coordinates=None, radius=200, precip_type=None, width=800, height=800):
        """Initialize the data object."""

        if station_id:
            coordinates = get_station_coords(station_id)

        if precip_type:
            self.layer = layer[precip_type.lower()]
        elif datetime.date.today().month in range(4, 11):
            self.layer = layer['rain']
        else:
            self.layer = layer['snow']

        self.bbox = get_bounding_box(radius, coordinates[0], coordinates[1])
        self.width = width
        self.height = height

        url = basemap_url.format(south=self.bbox[0],
                                 west=self.bbox[1],
                                 north=self.bbox[2],
                                 east=self.bbox[3],
                                 width=self.width,
                                 height=self.height)
        self.base_bytes = requests.get(url).content

        self.timestamp = datetime.datetime.now()

    def get_dimensions(self):
        """Get time range of available data."""
        capabilities_xml = requests.get(capabilities_url.format(layer=self.layer)).text
        capabilities_tree = et.fromstring(capabilities_xml, parser=et.XMLParser(encoding="utf-8"))
        dimension_string = capabilities_tree.find(dimension_xpath.format(layer=self.layer),
                                                  namespaces=wms_namespace).text
        start, end = [dateutil.parser.isoparse(t) for t in dimension_string.split('/')[:2]]
        self.timestamp = end.isoformat()
        return start, end

    def assemble_url(self, url_time):
        """Construct WMS query URL."""
        url = radar_url.format(south=self.bbox[0],
                               west=self.bbox[1],
                               north=self.bbox[2],
                               east=self.bbox[3],
                               width=self.width,
                               height=self.height,
                               layer=self.layer,
                               time=url_time.strftime('%Y-%m-%dT%H:%M:00Z'))
        return url

    def combine_layers(self, radar_bytes):
        """Add radar overlay to base layer."""
        frame_bytesio = BytesIO()
        base = Image.open(BytesIO(self.base_bytes)).convert('RGBA')
        radar = Image.open(BytesIO(radar_bytes)).convert('RGBA')
        base.alpha_composite(radar)
        blend = Image.blend(base, radar, 0)
        blend.save(frame_bytesio, 'GIF')
        return frame_bytesio.getvalue()

    def get_latest_frame(self):
        """Get the latest image from Environment Canada."""
        start, end = self.get_dimensions()
        radar = requests.get(self.assemble_url(end)).content
        return self.combine_layers(radar)

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

        frames = [self.combine_layers(f.content) for f in sorted(responses, key=lambda f: f.url)]

        for f in range(3):
            frames.append(frames[-1])

        """Assemble animated GIF."""
        gif_frames = [imageio.imread(f) for f in frames]
        gif_bytes = imageio.mimwrite(imageio.RETURN_BYTES, gif_frames, format='GIF', fps=10)
        return gif_bytes
