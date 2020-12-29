import logging
import re
import xml.etree.ElementTree as et

from geopy import distance
from ratelimit import limits, RateLimitException
import requests

SITE_LIST_URL = 'https://dd.weather.gc.ca/citypage_weather/docs/site_list_en.csv'
AQHI_SITE_LIST_URL = 'https://dd.weather.gc.ca/air_quality/doc/AQHI_XML_File_List.xml'

WEATHER_URL = 'https://dd.weather.gc.ca/citypage_weather/xml/{}_{}.xml'
AQHI_OBSERVATION_URL = 'https://dd.weather.gc.ca/air_quality/aqhi/{}/observation/realtime/xml/AQ_OBS_{}_CURRENT.xml'
AQHI_FORECAST_URL = 'https://dd.weather.gc.ca/air_quality/aqhi/{}/forecast/realtime/xml/AQ_FCST_{}_CURRENT.xml'

LOG = logging.getLogger(__name__)

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

aqhi_meta = {
    'label': {
        'english': 'Air Quality Health Index',
        'french': 'Cote air santé'
    }
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


def ignore_ratelimit_error(fun):
    def res(*args, **kwargs):
        try:
            return fun(*args, **kwargs)
        except RateLimitException:
            return None
    return res


class ECData(object):

    """Get weather data from Environment Canada."""

    def __init__(self,
                 station_id=None,
                 coordinates=None,
                 language='english'):
        """Initialize the data object."""
        self.language = language
        self.language_abr = language[:2].upper()
        self.zone_name_tag = 'name_%s_CA' % self.language_abr.lower()
        self.region_name_tag = 'name%s' % self.language_abr.title()

        self.metadata = {}
        self.conditions = {}
        self.alerts = {}
        self.daily_forecasts = []
        self.hourly_forecasts = []
        self.aqhi = {}
        self.forecast_time = ''
        self.aqhi_id = None

        if station_id:
            self.station_id = station_id
        else:
            self.station_id = self.closest_site(coordinates[0],
                                                coordinates[1])

        self.update()

    @ignore_ratelimit_error
    @limits(calls=2, period=60)
    def update(self):
        """Get the latest data from Environment Canada."""
        try:
            weather_result = requests.get(WEATHER_URL.format(self.station_id,
                                                         self.language[0]),
                                      timeout=10)
        except requests.exceptions.RequestException as e:
            LOG.warning("Unable to retrieve weather forecast: %s", e)
            return

        weather_xml = weather_result.content.decode('iso-8859-1')
        weather_tree = et.fromstring(weather_xml)

        # Update metadata
        for m, meta in metadata_meta.items():
            element = weather_tree.find(meta['xpath'])
            if element is not None:
                self.metadata[m] = weather_tree.find(meta['xpath']).text
            else:
                self.metadata[m] = None

        # Update current conditions
        def get_condition(meta):
            condition = {}

            element = weather_tree.find(meta['xpath'])

            if element is not None:
                if meta.get('attribute'):
                    condition['value'] = element.attrib.get(meta['attribute'])
                else:
                    condition['value'] = element.text
                    if element.attrib.get('units'):
                        condition['unit'] = element.attrib.get('units')
            return condition

        for c, meta in conditions_meta.items():
            self.conditions[c] = {'label': meta[self.language]}
            self.conditions[c].update(get_condition(meta))

        # Update text summary
        period = get_condition(summary_meta['forecast_period'])['value']
        summary = get_condition(summary_meta['text_summary'])['value']

        self.conditions['text_summary'] = {
            'label': summary_meta['label'][self.language],
            'value': '. '.join([period, summary])
        }

        # Update alerts
        for category, meta in alerts_meta.items():
            self.alerts[category] = {'value': [],
                                     'label': meta[self.language]['label']}

        alert_elements = weather_tree.findall('./warnings/event')

        for a in alert_elements:
            title = a.attrib.get('description').strip()
            for category, meta in alerts_meta.items():
                category_match = re.search(meta[self.language]['pattern'], title)
                if category_match:
                    alert = {'title': title.title(),
                             'date': a.find('./dateTime[last()]/textSummary').text,
                             }
                    self.alerts[category]['value'].append(alert)

        # Update daily forecasts
        self.forecast_time = weather_tree.findtext('./forecastGroup/dateTime/timeStamp')
        self.daily_forecasts = []
        self.hourly_forecasts = []

        for f in weather_tree.findall('./forecastGroup/forecast'):
            self.daily_forecasts.append({
                'period': f.findtext('period'),
                'text_summary': f.findtext('textSummary'),
                'icon_code': f.findtext('./abbreviatedForecast/iconCode'),
                'temperature': f.findtext('./temperatures/temperature'),
                'temperature_class': f.find('./temperatures/temperature').attrib.get('class'),
                'precip_probability': f.findtext('./abbreviatedForecast/pop') or "0"
            })

        # Update hourly forecasts
        for f in weather_tree.findall('./hourlyForecastGroup/hourlyForecast'):
            self.hourly_forecasts.append({
                'period': f.attrib.get('dateTimeUTC'),
                'condition': f.findtext('./condition'),
                'temperature': f.findtext('./temperature'),
                'icon_code': f.findtext('./iconCode'),
                'precip_probability': f.findtext('./lop')  or "0",
            })

        # Update AQHI current condition

        if self.aqhi_id is None:
            lat = weather_tree.find('./location/name').attrib.get('lat')[:-1]
            lon = weather_tree.find('./location/name').attrib.get('lon')[:-1]
            aqhi_coordinates = (float(lat), float(lon) * -1)
            self.aqhi_id = self.closest_aqhi(aqhi_coordinates[0], aqhi_coordinates[1])

        success = True
        try:
            aqhi_result = requests.get(AQHI_OBSERVATION_URL.format(self.aqhi_id[0],
                                                               self.aqhi_id[1]),
                                   timeout=10)
        except requests.exceptions.RequestException as e:
            LOG.warning("Unable to retrieve current AQHI observation: %s", e)
            success = False

        if not success or aqhi_result.status_code == 404:
            self.aqhi['current'] = None
        else:
            aqhi_xml = aqhi_result.content.decode("utf-8")
            aqhi_tree = et.fromstring(aqhi_xml)

            element = aqhi_tree.find('airQualityHealthIndex')
            if element is not None:
                self.aqhi['current'] = element.text
            else:
                self.aqhi['current'] = None

            self.conditions['air_quality'] = {
                'label': aqhi_meta['label'][self.language],
                'value': self.aqhi['current']
            }

            element = aqhi_tree.find('./dateStamp/UTCStamp')
            if element is not None:
                self.aqhi['utc_time'] = element.text
            else:
                self.aqhi['utc_time'] = None

        # Update AQHI forecasts
        success = True
        try:
            aqhi_result = requests.get(AQHI_FORECAST_URL.format(self.aqhi_id[0],
                                                            self.aqhi_id[1]),
                                   timeout=10)
        except requests.exceptions.RequestException as e:
            LOG.warning("Unable to retrieve forecast AQHI observation: %s", e)
            success = False

        if not success or aqhi_result.status_code == 404:
            self.aqhi['forecasts'] = None
        else:
            aqhi_xml = aqhi_result.content.decode("ISO-8859-1")
            aqhi_tree = et.fromstring(aqhi_xml)

            self.aqhi['forecasts'] = {'daily': [],
                                      'hourly': []}

            # Update AQHI daily forecasts
            period = None
            for f in aqhi_tree.findall("./forecastGroup/forecast"):
                for p in f.findall("./period"):
                    if self.language_abr == p.attrib["lang"]:
                        period = p.attrib["forecastName"]
                self.aqhi['forecasts']['daily'].append(
                    {
                        "period": period,
                        "aqhi": f.findtext("./airQualityHealthIndex"),
                    }
                )

            # Update AQHI hourly forecasts
            for f in aqhi_tree.findall("./hourlyForecastGroup/hourlyForecast"):
                self.aqhi['forecasts']['hourly'].append(
                    {"period": f.attrib["UTCTime"], "aqhi": f.text}
                )

    def get_ec_sites(self):
        """Get list of all sites from Environment Canada, for auto-config."""
        import csv
        import io

        sites = []

        try:
            sites_result = requests.get(SITE_LIST_URL, timeout=10)
            sites_csv_string = sites_result.text
        except requests.exceptions.RequestException as e:
            LOG.warning("Unable to retrieve site list csv: %s", e)
            return sites

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

    def get_aqhi_regions(self):
        """Get list of all AQHI regions from Environment Canada, for auto-config."""
        regions = []
        try:
            result = requests.get(AQHI_SITE_LIST_URL, timeout=10)
        except requests.exceptions.RequestException as e:
            LOG.warning("Unable to retrieve AQHI regions: %s", e)
            return regions

        site_xml = result.content.decode("utf-8")
        xml_object = et.fromstring(site_xml)

        for zone in xml_object.findall("./EC_administrativeZone"):
            _zone_attribs = zone.attrib
            _zone_attrib = {
                "abbreviation": _zone_attribs["abreviation"],
                "zone_name": _zone_attribs[self.zone_name_tag],
            }
            for region in zone.findall("./regionList/region"):
                _region_attribs = region.attrib

                _region_attrib = {"region_name": _region_attribs[self.region_name_tag],
                                  "cgndb": _region_attribs["cgndb"],
                                  "latitude": float(_region_attribs["latitude"]),
                                  "longitude": float(_region_attribs["longitude"])}
                _children = list(region)
                for child in _children:
                    _region_attrib[child.tag] = child.text
                _region_attrib.update(_zone_attrib)
                regions.append(_region_attrib)
        return regions

    def closest_aqhi(self, lat, lon):
        """Return the AQHI region and site ID of the closest site."""
        region_list = self.get_aqhi_regions()

        def site_distance(site):
            """Calculate distance to a region."""
            return distance.distance(
                (lat, lon), (site["latitude"], site["longitude"])
            )
        closest = min(region_list, key=site_distance)

        return closest['abbreviation'], closest['cgndb']
