import csv
import io

from dateutil.parser import isoparse
from geopy import distance
from ratelimit import limits, RateLimitException
import requests

SITE_LIST_URL = 'https://dd.weather.gc.ca/hydrometric/doc/hydrometric_StationList.csv'
READINGS_URL = 'https://dd.weather.gc.ca/hydrometric/csv/{prov}/hourly/{prov}_{station}_hourly_hydrometric.csv'


def ignore_ratelimit_error(fun):
    def res(*args, **kwargs):
        try:
            return fun(*args, **kwargs)
        except RateLimitException:
            return None
    return res


class ECHydro(object):

    """Get hydrometric data from Environment Canada."""

    def __init__(self,
                 province=None,
                 station=None,
                 coordinates=None):
        """Initialize the data object."""
        self.measurements = {}
        self.timestamp = None
        self.location = None

        if province and station:
            self.province = province
            self.station = station
        else:
            closest = self.closest_site(coordinates[0], coordinates[1])
            self.province = closest['Prov']
            self.station = closest['ID']
            self.location = closest['Name'].title()

        self.update()

    @ignore_ratelimit_error
    @limits(calls=2, period=60)
    def update(self):
        """Get the latest data from Environment Canada."""
        hydro_csv_response = requests.get(READINGS_URL.format(prov=self.province,
                                                              station=self.station),
                                          timeout=10)
        hydro_csv_string = hydro_csv_response.content.decode('utf-8-sig')
        hydro_csv_stream = io.StringIO(hydro_csv_string)

        header = [h.split('/')[0].strip() for h in hydro_csv_stream.readline().split(',')]
        readings_reader = csv.DictReader(hydro_csv_stream, fieldnames=header)

        readings = [r for r in readings_reader]
        if len(readings) > 0:
            latest = readings[-1]

            if latest['Water Level'] != '':
                self.measurements['water_level'] = {
                    'label': 'Water Level',
                    'value': float(latest['Water Level']),
                    'unit': 'm'
                }

            if latest['Discharge'] != '':
                self.measurements['discharge'] = {
                    'label': 'Discharge',
                    'value': float(latest['Discharge']),
                    'unit': 'm³/s'
                }

            self.timestamp = isoparse(readings[-1]['Date'])

    @staticmethod
    def get_hydro_sites():

        """Get list of all sites from Environment Canada, for auto-config."""

        sites = []

        sites_csv_bytes = requests.get(SITE_LIST_URL, timeout=10).content
        sites_csv_string = sites_csv_bytes.decode('utf-8-sig')
        sites_csv_stream = io.StringIO(sites_csv_string)

        header = [h.split('/')[0].strip() for h in sites_csv_stream.readline().split(',')]
        sites_reader = csv.DictReader(sites_csv_stream, fieldnames=header)

        for site in sites_reader:
            site['Latitude'] = float(site['Latitude'])
            site['Longitude'] = float(site['Longitude'])
            sites.append(site)

        return sites

    def closest_site(self, lat, lon):
        """Return the province/site_code of the closest station to our lat/lon."""
        site_list = self.get_hydro_sites()

        def site_distance(site):
            """Calculate distance to a site."""
            return distance.distance((lat, lon),
                                     (site['Latitude'], site['Longitude']))

        closest = min(site_list, key=site_distance)

        return closest
