import datetime
from io import BytesIO
from PIL import Image
import re
import xml.etree.ElementTree as et

from bs4 import BeautifulSoup
from geopy import distance
import imageio
import requests
from requests_futures.sessions import FuturesSession


class ECData(object):
    SITE_LIST_URL = 'http://dd.weatheroffice.ec.gc.ca/citypage_weather/docs/site_list_en.csv'
    XML_URL_BASE = 'http://dd.weatheroffice.ec.gc.ca/citypage_weather/xml/{}_e.xml'
    value_paths = {
        'temperature': './currentConditions/temperature',
        'dewpoint': './currentConditions/dewpoint',
        'wind_chill': './currentConditions/windChill',
        'humidex': './currentConditions/humidex',
        'pressure': './currentConditions/pressure',
        'humidity': './currentConditions/relativeHumidity',
        'visibility': './currentConditions/visibility',
        'condition': './currentConditions/condition',
        'wind_speed': './currentConditions/wind/speed',
        'wind_gust': './currentConditions/wind/gust',
        'wind_dir': './currentConditions/wind/direction',
        'wind_bearing': './currentConditions/wind/bearing',
        'high_temp': './forecastGroup/forecast/temperatures/temperature[@class="high"]',
        'low_temp': './forecastGroup/forecast/temperatures/temperature[@class="low"]',
        'pop': './forecastGroup/forecast/abbreviatedForecast/pop',
        'timestamp': './currentConditions/dateTime/timeStamp',
        'location': './location/name',
        'station': './currentConditions/station',
        'icon_code': './currentConditions/iconCode'
    }

    attribute_paths = {
        'tendency': {'xpath': './currentConditions/pressure',
                     'attribute': 'tendency'}
    }

    alert_patterns = {
        'warnings': '.*WARNING((?!ENDED).)*$',
        'watches': '.*WATCH((?!ENDED).)*$',
        'advisories': '.*ADVISORY((?!ENDED).)*$',
        'statements': '.*STATEMENT((?!ENDED).)*$',
        'endings': '.*ENDED'
    }

    """Get data from Environment Canada."""

    def __init__(self, station_id=None, coordinates=None):
        """Initialize the data object."""
        if station_id:
            self.station_id = station_id
        else:
            self.station_id = self.closest_site(coordinates[0],
                                                coordinates[1])
        self.conditions = {}
        self.alerts = {}
        self.daily_forecasts = []
        self.hourly_forecasts = []
        self.forecast_time = ''

        self.update()

    def update(self):
        """Get the latest data from Environment Canada."""
        result = requests.get(self.XML_URL_BASE.format(self.station_id),
                              timeout=10)
        site_xml = result.content.decode('iso-8859-1')
        xml_object = et.fromstring(site_xml)

        # Update current conditions
        self.conditions = {}

        for condition, xpath in self.value_paths.items():
            result = xml_object.findtext(xpath)
            if result:
                self.conditions[condition] = result

        for condition, v in self.attribute_paths.items():
            element = xml_object.find(v['xpath'])
            value = element.attrib.get(v['attribute'])
            if value:
                self.conditions[condition] = value

        # Update alerts
        alert_elements = xml_object.findall('./warnings/event')
        alert_list = [e.attrib.get('description').strip() for e in alert_elements]

        for category, pattern in self.alert_patterns.items():
            self.alerts[category] = [re.search(pattern, a).group(0) for a in alert_list if re.search(pattern, a)]

        # Update daily forecasts
        self.forecast_time = xml_object.findtext('./forecastGroup/dateTime/timeStamp')

        self.daily_forecasts = []
        for f in xml_object.findall('./forecastGroup/forecast'):
            self.daily_forecasts.append({
                'period': f.findtext('period'),
                'text_summary': f.findtext('textSummary'),
                'icon_code': f.findtext('./abbreviatedForecast/iconCode'),
                'temperature': f.findtext('./temperatures/temperature'),
                'temperature_class': f.find('./temperatures/temperature').attrib.get('class')
            })

        # Update hourly forecasts
        self.hourly_forecasts = []
        for f in xml_object.findall('./hourlyForecastGroup/hourlyForecast'):
            self.hourly_forecasts.append({
                'period': f.attrib.get('dateTimeUTC'),
                'condition': f.findtext('./condition'),
                'temperature': f.findtext('./temperature'),
                'icon_code': f.findtext('./iconCode'),
                'precip_probability': f.findtext('./lop'),
            })

    def get_ec_sites(self):
        """Get list of all sites from Environment Canada, for auto-config."""
        import csv
        import io

        sites = []

        sites_csv_string = requests.get(self.SITE_LIST_URL, timeout=10).text
        sites_csv_stream = io.StringIO(sites_csv_string)

        sites_csv_stream.seek(0)
        next(sites_csv_stream)

        sites_reader = csv.DictReader(sites_csv_stream)

        for site in sites_reader:
            if site['Province Codes'] != 'HEF':
                site['Latitude'] = float(site['Latitude'].replace('N', ''))
                site['Longitude'] = -1 * float(site['Longitude'].replace('W', ''))
                sites.append(site)

        return sites

    def closest_site(self, lat, lon):
        """Return the province/site_code of the closest station to our lat/lon."""
        site_list = self.get_ec_sites()

        def site_distance(site):
            """Calculate distance to a site."""
            return distance.distance((lat, lon), (site['Latitude'], site['Longitude']))

        closest = min(site_list, key=site_distance)

        return '{}/{}'.format(closest['Province Codes'], closest['Codes'])


class ECRadar(object):
    IMAGES_URL = 'http://dd.weatheroffice.ec.gc.ca/radar/PRECIPET/GIF/{0}/?C=M;O=D'
    FRAME_URL = 'http://dd.weatheroffice.ec.gc.ca/radar/PRECIPET/GIF/{0}/{1}'
    CITIES_URL = 'https://weather.gc.ca/cacheable/images/radar/layers/default_cities/{0}_towns.gif'
    ROADS_URL = 'https://weather.gc.ca/cacheable/images/radar/layers/roads/{0}_roads.gif'

    def __init__(self, station_id=None, coordinates=None, precip_type=None):
        """Initialize the data object."""
        self.sites = self.get_radar_sites()

        if station_id:
            self.station_code = station_id
        elif coordinates:
            self.station_code = self.closest_site(coordinates[0], coordinates[1])[0]

        self.station_name = self.sites[self.station_code]['name']

        if precip_type:
            self.user_precip_type = precip_type
        else:
            self.user_precip_type = None

        cities_bytes = requests.get(self.CITIES_URL.format(self.station_code.lower())).content
        self.cities = Image.open(BytesIO(cities_bytes)).convert('RGBA')
        roads_bytes = requests.get(self.ROADS_URL.format(self.station_code.upper())).content
        self.roads = Image.open(BytesIO(roads_bytes)).convert('RGBA')

    def get_precip_type(self):
        """Determine the precipitation type"""
        if self.user_precip_type:
            return self.user_precip_type
        elif datetime.date.today().month in range(4, 11):
            return 'RAIN'
        else:
            return 'SNOW'

    def get_frames(self, count):
        """Get a list of images from Environment Canada."""
        soup = BeautifulSoup(requests.get(self.IMAGES_URL.format(self.station_code)).text, 'html.parser')
        image_links = [tag['href'] for tag in soup.find_all('a') if '.gif' in tag['href']]

        if len([i for i in image_links[:8] if 'COMP' in i]) > 4:
            image_string = '_'.join([self.station_code, 'COMP_PRECIPET', self.get_precip_type() + '.gif'])
        else:
            image_string = '_'.join([self.station_code, 'PRECIPET', self.get_precip_type() + '.gif'])

        images = [tag['href'] for tag in soup.find_all('a') if image_string in tag['href']]

        futures = []
        session = FuturesSession(max_workers=count)

        for i in reversed(images[:count]):
            url = self.FRAME_URL.format(self.station_code, i)
            futures.append(session.get(url=url).result().content)

        def add_layers(frame):
            frame_bytesio = BytesIO()
            base = Image.open(BytesIO(frame)).convert('RGBA')
            base.alpha_composite(self.roads)
            base.alpha_composite(self.cities)
            base.save(frame_bytesio, 'GIF')
            frame_bytesio.seek(0)
            return frame_bytesio.read()

        frames = [add_layers(f) for f in futures if f[0:3] == b'GIF']

        """Repeat last frame."""
        for i in range(0, 2):  # pylint: disable=unused-variable
            frames.append(frames[count - 1])

        return frames

    def get_latest_frame(self):
        """Get the latest image from Environment Canada."""
        return self.get_frames(1)[0]

    def get_loop(self):
        """Build an animated GIF of recent radar images."""
        if len(self.station_code) == 5:
            count = 20
            fps = 10
        else:
            count = 12
            fps = 6

        frames = self.get_frames(count)
        gifs = [imageio.imread(f) for f in frames]

        return imageio.mimwrite(imageio.RETURN_BYTES,
                                gifs,
                                format='GIF',
                                fps=fps)

    def get_radar_sites(self):
        """Get list of radar sites from Wikipedia."""
        xml_string = requests.get('https://tools.wmflabs.org/kmlexport?article=Canadian_weather_radar_network').text
        root = et.fromstring(xml_string)
        namespace = {'ns': 'http://earth.google.com/kml/2.1'}
        folder = root.find('ns:Document/ns:Folder', namespace)

        site_dict = {}

        for site in folder.findall('ns:Placemark', namespace):
            name_parts = site.find('ns:name', namespace).text.split(' - ')
            name = name_parts[1]
            code = name_parts[0]
            if len(code) == 4:
                code = code[1:]
            lat = float(site.find('ns:Point/ns:coordinates', namespace).text.split(',')[1])
            lon = float(site.find('ns:Point/ns:coordinates', namespace).text.split(',')[0])

            site_dict[code] = {'name': name,
                               'lat': lat,
                               'lon': lon}
        return site_dict

    def closest_site(self, lat, lon):
        """Return the site code of the closest radar to our lat/lon."""

        def site_distance(site):
            """Calculate distance to a site."""
            return distance.distance((lat, lon), (site[1]['lat'], site[1]['lon']))

        closest = min(self.sites.items(), key=site_distance)

        return closest
