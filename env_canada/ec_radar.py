from concurrent.futures import as_completed
import datetime
from io import BytesIO
from PIL import Image
import xml.etree.ElementTree as et

import dateutil.parser
import imageio
import mercantile
import requests
from requests_futures.sessions import FuturesSession

zoom = 6
width, height = (500, 500)

# Mapbox

access_token = 'pk.eyJ1IjoibWljaGFlbGRhdmllIiwiYSI6ImNrOWI5Z3Y2aDBjY2ozZm50NHhpdXR6M28ifQ.IfNY1iN_NgMI9f8dj-7HKw'
mapbox_url = 'https://api.mapbox.com/styles/v1/mapbox/light-v10/static/{lng},{lat},{zoom},0/{width}x{height}?access_token={token}'

# Environment Canada

layer = {
    'rain': 'RADAR_1KM_RRAI',
    'snow': 'RADAR_1KM_RSNO'
}

capabilities_url = 'https://geo.weather.gc.ca/geomet/?lang=en&service=WMS&version=1.3.0&request=GetCapabilities&LAYER={layer}'
wms_namespace = {'wms': 'http://www.opengis.net/wms'}
dimension_xpath = './/wms:Layer[wms:Name="{layer}"]/wms:Dimension'

radar_url = 'https://geo.weather.gc.ca/geomet?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&BBOX={south},{west},{north},{east}&CRS=EPSG:4326&WIDTH={width}&HEIGHT={height}&LAYERS={layer}&FORMAT=image/png&TIME={time}'


class ECRadar(object):
    def __init__(self, coordinates=None, precip_type=None):
        """Initialize the data object."""
        self.tile = mercantile.tile(lng=coordinates[1], lat=coordinates[0], zoom=zoom)
        self.bounds = mercantile.bounds(self.tile)

        if precip_type:
            self.layer = layer[precip_type]
        elif datetime.date.today().month in range(4, 11):
            self.layer = layer['rain']
        else:
            self.layer = layer['snow']

        self.base_bytes = requests.get(mapbox_url.format(lng=coordinates[1],
                                                         lat=coordinates[0],
                                                         zoom=zoom,
                                                         width=width,
                                                         height=height,
                                                         token=access_token)).content
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
        url = radar_url.format(south=self.bounds.south,
                               west=self.bounds.west,
                               north=self.bounds.north,
                               east=self.bounds.east,
                               width=width,
                               height=height,
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
        frames.append(frames[-1])

        """Assemble animated GIF."""
        gif_frames = [imageio.imread(f) for f in frames]
        gif_bytes = imageio.mimwrite(imageio.RETURN_BYTES, gif_frames, format='GIF', fps=10)
        return gif_bytes
