import re
import xml.etree.ElementTree as et

from bs4 import BeautifulSoup
from geopy import distance
import requests


class ECData(object):
    SITE_LIST_URL = 'http://dd.weatheroffice.ec.gc.ca/citypage_weather/docs/site_list_en.csv'
    XML_URL_BASE = 'http://dd.weatheroffice.ec.gc.ca/citypage_weather/xml/{}_{}.xml'
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
        'text_summary': './forecastGroup/forecast/textSummary',
        'high_temp': './forecastGroup/forecast/temperatures/temperature[@class="high"]',
        'low_temp': './forecastGroup/forecast/temperatures/temperature[@class="low"]',
        'pop': './forecastGroup/forecast/abbreviatedForecast/pop',
        'timestamp': './currentConditions/dateTime/timeStamp',
        'location': './location/name',
        'station': './currentConditions/station',
        'icon_code': './currentConditions/iconCode'
    }

    attribute_paths = {
        'forecast_period': {
            'xpath': './forecastGroup/forecast/period',
            'attribute': 'textForecastName'
        },
        'tendency': {
            'xpath': './currentConditions/pressure',
            'attribute': 'tendency'
        }
    }

    alert_patterns = {
        'english': {
            'warnings': '.*WARNING((?!ENDED).)*$',
            'watches': '.*WATCH((?!ENDED).)*$',
            'advisories': '.*ADVISORY((?!ENDED).)*$',
            'statements': '.*STATEMENT((?!ENDED).)*$',
            'endings': '.*ENDED'
        },
        'french': {
            'warnings': '.*ALERTE((?!TERMINÉ).)*$',
            'watches': '.*VEILLE((?!TERMINÉ).)*$',
            'advisories': '.*AVIS((?!TERMINÉ).)*$',
            'statements': '.*BULLETIN((?!TERMINÉ).)*$',
            'endings': '.*TERMINÉE?'
        }
    }

    """Get data from Environment Canada."""

    def __init__(self, station_id=None, coordinates=None, language='english'):
        """Initialize the data object."""
        if station_id:
            self.station_id = station_id
        else:
            self.station_id = self.closest_site(coordinates[0],
                                                coordinates[1])
        self.language = language
        self.conditions = {}
        self.alerts = {}
        self.daily_forecasts = []
        self.hourly_forecasts = []
        self.forecast_time = ''

        self.update()

    def update(self):
        """Get the latest data from Environment Canada."""
        result = requests.get(self.XML_URL_BASE.format(self.station_id,
                                                       self.language[0]),
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
            if element is not None:
                value = element.attrib.get(v['attribute'])
                if value:
                    self.conditions[condition] = value

        # Update alerts
        alert_elements = xml_object.findall('./warnings/event')
        alert_list = [e.attrib.get('description').strip() for e in alert_elements]

        if alert_list:
            alert_url = xml_object.find('./warnings').attrib.get('url')
            alert_html = requests.get(url=alert_url).content
            alert_soup = BeautifulSoup(alert_html, 'html.parser')

            date_pattern = 'p:contains("{}") span'
            detail_pattern = 'p:contains("{}") ~ p'

            for category, pattern in self.alert_patterns[self.language].items():
                self.alerts[category] = []
                for a in alert_list:
                    title_match = re.search(pattern, a)
                    if title_match:
                        alert = {'title': a,
                                 'date': '',
                                 'detail': ''}
                        title = title_match.group(0).capitalize()
                        alert.update({'title': title.title()})

                        if 'terminé' in title:
                            title = re.sub('terminé', 'est terminé', title)

                        date_match = alert_soup.select(date_pattern.format(title))
                        if date_match:
                            alert.update({'date': date_match[0].text})

                        detail_match = alert_soup.select(detail_pattern.format(title))
                        if detail_match:
                            alert.update({'detail': detail_match[0].text})

                        self.alerts[category].append(alert)

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
