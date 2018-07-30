import datetime
import xml.etree.ElementTree as et

from geopy import distance
import requests


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
        'warning': {'xpath': './warnings/event',
                    'attribute': 'description'},
        'tendency': {'xpath': './currentConditions/pressure',
                     'attribute': 'tendency'}
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
        for condition, xpath in self.value_paths.items():
            result = xml_object.findtext(xpath)
            if result:
                self.conditions[condition] = result

        for condition, v in self.attribute_paths.items():
            element = xml_object.find(v['xpath'])
            if element:
                value = element.attrib.get(v['attribute'])
                if value:
                    self.conditions[condition] = value

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
    FRAME_URL = 'http://dd.weatheroffice.ec.gc.ca/radar/' \
                'PRECIPET/GIF/{0}/{1}_{0}_{2}PRECIPET_RAIN.gif'
    LOOP_FRAMES = 12
    LOOP_FPS = 6

    def __init__(self, station_id=None, coordinates=None):
        """Initialize the data object."""
        self.sites = self.get_radar_sites()
        if station_id:
            self.station_code = station_id
        else:
            self.station_code = self.closest_site(coordinates[0], coordinates[1])[0]
        self.station_name = self.sites[self.station_code]['name']
        self.image_bytes = None
        self.composite = self.detect_composite()

    def detect_composite(self):
        """Detect if a station is returning regular or composite images."""
        url = self.FRAME_URL.format(self.station_code, self.frame_time(10), '')
        if requests.get(url=url).status_code != 404:
            return ''
        else:
            url = self.FRAME_URL.format(self.station_code, self.frame_time(10), 'COMP_')
            if requests.get(url=url).status_code != 404:
                return 'COMP_'
        return None

    @staticmethod
    def frame_time(mins_ago):
        """Return the timestamp of a frame from at least x minutes ago."""
        time_object = datetime.datetime.utcnow() - datetime.timedelta(minutes=mins_ago)
        time_string = time_object.strftime('%Y%m%d%H%M')
        time_string = time_string[:-1] + '0'
        return time_string

    def get_frames(self, count):
        """Get a list of images from Environment Canada."""
        from requests_futures.sessions import FuturesSession

        frames = []
        futures = []
        session = FuturesSession(max_workers=5)

        for mins_ago in range(10 * count, 0, -10):
            time_string = self.frame_time(mins_ago)
            url = self.FRAME_URL.format(self.station_code,
                                        time_string,
                                        self.composite)
            futures.append(session.get(url=url))

        for future in futures:
            frames.append(future.result().content)
        for i in range(0, 2):             # pylint: disable=unused-variable
            frames.append(frames[count - 1])

        return frames

    def get_latest_frame(self):
        """Get the latest image from Environment Canada."""
        return self.get_frames(1)[0]

    def get_loop(self):
        """Build an animated GIF of recent radar images."""
        import imageio

        frames = self.get_frames(self.LOOP_FRAMES)
        gifs = []

        for frame in frames:
            gifs.append(imageio.imread(frame))

        return imageio.mimwrite(imageio.RETURN_BYTES,
                                gifs, format='GIF', fps=self.LOOP_FPS)

    def get_radar_sites(self):
        """Get list of radar sites from Wikipedia."""
        xml_string = requests.get('https://tools.wmflabs.org/kmlexport?article=Canadian_weather_radar_network').text
        root = et.fromstring(xml_string)
        namespace = {'ns': 'http://earth.google.com/kml/2.1'}
        folder = root.find('ns:Document/ns:Folder', namespace)

        site_dict = {}

        for site in folder.findall('ns:Placemark', namespace):
            code = site.find('ns:name', namespace).text[1:4]
            name = site.find('ns:name', namespace).text[7:]
            lat = float(site.find('ns:Point/ns:coordinates',
                                  namespace).text.split(',')[1])
            lon = float(site.find('ns:Point/ns:coordinates',
                                  namespace).text.split(',')[0])

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
