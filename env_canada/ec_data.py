import re
import xml.etree.ElementTree as et

from bs4 import BeautifulSoup
from geopy import distance
import requests


class ECData(object):
    SITE_LIST_URL = 'http://dd.weatheroffice.ec.gc.ca/citypage_weather/docs/site_list_en.csv'
    XML_URL_BASE = 'http://dd.weatheroffice.ec.gc.ca/citypage_weather/xml/{}_{}.xml'
    conditions_meta = {
        'temperature': {
            'xpath': './currentConditions/temperature',
            'english': 'Temperature',
            'french': 'Température'
        },
        'dewpoint': {
            'xpath': './currentConditions/dewpoint',
            'english': 'Dew Point',
            'french': 'Point de rosée'
        },
        'wind_chill': {
            'xpath': './currentConditions/windChill',
            'english': 'Wind Chill',
            'french': 'Refroidissement éolien'
        },
        'humidex': {
            'xpath': './currentConditions/humidex',
            'english': 'Humidex',
            'french': 'Humidex'
        },
        'pressure': {
            'xpath': './currentConditions/pressure',
            'english': 'Pressure',
            'french': 'Pression'
        },
        'tendency': {
            'xpath': './currentConditions/pressure',
            'attribute': 'tendency',
            'english': 'Tendency',
            'french': 'Tendance'
        },
        'humidity': {
            'xpath': './currentConditions/relativeHumidity',
            'english': 'Humidity',
            'french': 'Humidité'
        },
        'visibility': {
            'xpath': './currentConditions/visibility',
            'english': 'Visibility',
            'french': 'Visibilité'
        },
        'condition': {
            'xpath': './currentConditions/condition',
            'english': 'Condition',
            'french': 'Condition'
        },
        'wind_speed': {
            'xpath': './currentConditions/wind/speed',
            'english': 'Wind Speed',
            'french': 'Vitesse de vent'
        },
        'wind_gust': {
            'xpath': './currentConditions/wind/gust',
            'english': 'Wind Gust',
            'french': 'Rafale de vent'
        },
        'wind_dir': {
            'xpath': './currentConditions/wind/direction',
            'english': 'Wind Direction',
            'french': 'Direction de vent'
        },
        'wind_bearing': {
            'xpath': './currentConditions/wind/bearing',
            'english': 'Wind Bearing',
            'french': 'Palier de vent'
        },
        'high_temp': {
            'xpath': './forecastGroup/forecast/temperatures/temperature[@class="high"]',
            'english': 'High Temperature',
            'french': 'Haute température'
        },
        'low_temp': {
            'xpath': './forecastGroup/forecast/temperatures/temperature[@class="low"]',
            'english': 'Low Temperature',
            'french': 'Basse température'
        },
        'uv_index': {
            'xpath': './forecastGroup/forecast/uv/index',
            'english': 'UV Index',
            'french': 'Indice UV'
        },
        'pop': {
            'xpath': './forecastGroup/forecast/abbreviatedForecast/pop',
            'english': 'Chance of Precip.',
            'french': 'Probabilité d\'averses'
        },
        'icon_code': {
            'xpath': './currentConditions/iconCode',
            'english': 'Icon Code',
            'french': 'Code icône'
        },
        'precip_yesterday': {
            'xpath': './yesterdayConditions/precip',
            'english': 'Precipitation Yesterday',
            'french': 'Précipitation d\'hier'
        },
    }

    summary_meta = {
        'forecast_period': {
            'xpath': './forecastGroup/forecast/period',
            'attribute': 'textForecastName',
        },
        'text_summary': {
            'xpath': './forecastGroup/forecast/textSummary',
        },
        'label': {
            'english': 'Forecast',
            'french': 'Prévision'
        }
    }

    alerts_meta = {
        'warnings': {
            'english': {
                'label': 'Warnings',
                'pattern': '.*WARNING((?!ENDED).)*$'
            },
            'french': {
                'label': 'Alertes',
                'pattern': '.*(ALERTE|AVERTISSEMENT)((?!TERMINÉ).)*$'
            }
        },
        'watches': {
            'english': {
                'label': 'Watches',
                'pattern': '.*WATCH((?!ENDED).)*$'
            },
            'french': {
                'label': 'Veilles',
                'pattern': '.*VEILLE((?!TERMINÉ).)*$'
            }
        },
        'advisories': {
            'english': {
                'label': 'Advisories',
                'pattern': '.*ADVISORY((?!ENDED).)*$'
            },
            'french': {
                'label': 'Avis',
                'pattern': '.*AVIS((?!TERMINÉ).)*$'
            }
        },
        'statements': {
            'english': {
                'label': 'Statements',
                'pattern': '.*STATEMENT((?!ENDED).)*$'
            },
            'french': {
                'label': 'Bulletins',
                'pattern': '.*BULLETIN((?!TERMINÉ).)*$'
            }
        },
        'endings': {
            'english': {
                'label': 'Endings',
                'pattern': '.*ENDED'
            },
            'french': {
                'label': 'Terminaisons',
                'pattern': '.*TERMINÉE?'
            }
        }
    }

    metadata_meta = {
        'timestamp': {
            'xpath': './currentConditions/dateTime/timeStamp',
        },
        'location': {
            'xpath': './location/name',
        },
        'station': {
            'xpath': './currentConditions/station',
        },
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
        self.metadata = {}
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

        # Update metadata
        for m, meta in self.metadata_meta.items():
            self.metadata[m] = xml_object.find(meta['xpath']).text

        # Update current conditions
        def get_condition(meta):
            condition = {}

            element = xml_object.find(meta['xpath'])

            if element is not None:
                if meta.get('attribute'):
                    condition['value'] = element.attrib.get(meta['attribute'])
                else:
                    condition['value'] = element.text
                    if element.attrib.get('units'):
                        condition['unit'] = element.attrib.get('units')
            return condition

        for c, meta in self.conditions_meta.items():
            self.conditions[c] = {'label': meta[self.language]}
            self.conditions[c].update(get_condition(meta))

        # Update text summary
        period = get_condition(self.summary_meta['forecast_period'])['value']
        summary = get_condition(self.summary_meta['text_summary'])['value']

        self.conditions['text_summary'] = {
            'label': self.summary_meta['label'][self.language],
            'value': '. '.join([period, summary])
        }

        # Update alerts
        for category, meta in self.alerts_meta.items():
            self.alerts[category] = {'value': [],
                                     'label': meta[self.language]['label']}

        alert_elements = xml_object.findall('./warnings/event')
        alert_list = [e.attrib.get('description').strip() for e in alert_elements]

        if alert_list:
            alert_url = xml_object.find('./warnings').attrib.get('url')
            alert_html = requests.get(url=alert_url).content
            alert_soup = BeautifulSoup(alert_html, 'html.parser')

            for title in alert_list:
                for category, meta in self.alerts_meta.items():
                    category_match = re.search(meta[self.language]['pattern'], title)
                    if category_match:

                        alert = {'title': title.title(),
                                 'date': '',
                                 'detail': ''}

                        html_title = ''

                        for s in alert_soup('strong'):
                            if re.sub('terminé', 'est terminé', title.lower()) in s.text.lower():
                                html_title = s.text

                        date_pattern = 'p:contains("{}") span'
                        date_match = alert_soup.select(date_pattern.format(html_title))
                        if date_match:
                            alert.update({'date': date_match[0].text})

                        if category != 'endings':
                            detail_pattern = 'p:contains("{}") ~ p'
                            detail_match = alert_soup.select(detail_pattern.format(html_title))
                            if detail_match:
                                detail = re.sub(r'\.(?=\S)', '. ', detail_match[0].text)
                                alert.update({'detail': detail})

                        self.alerts[category]['value'].append(alert)

        # Update daily forecasts
        self.forecast_time = xml_object.findtext('./forecastGroup/dateTime/timeStamp')

        for f in xml_object.findall('./forecastGroup/forecast'):
            self.daily_forecasts.append({
                'period': f.findtext('period'),
                'text_summary': f.findtext('textSummary'),
                'icon_code': f.findtext('./abbreviatedForecast/iconCode'),
                'temperature': f.findtext('./temperatures/temperature'),
                'temperature_class': f.find('./temperatures/temperature').attrib.get('class')
            })

        # Update hourly forecasts
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
