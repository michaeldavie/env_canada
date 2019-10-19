import re
import xml.etree.ElementTree as et

from geopy import distance
from ratelimit import RateLimitException, limits
import requests

RATE_LIMIT_CALLS = 4
RATE_LIMIT_PERIOD = 60


def ignore_ratelimit_exception(fun):

    def res(*args, **kwargs):
        try:
            return fun(*args, **kwargs)
        except RateLimitException:
            return None

    return res


class AqhiData(object):
    XML_HOST = "https://dd.weather.gc.ca/"
    XML_LIST_URL = "%s/air_quality/doc/AQHI_XML_File_List.xml" % XML_HOST
    XML_URL_OBS = (
        "%s/air_quality/aqhi/{}/observation/realtime/xml/AQ_OBS_{}_CURRENT.xml"
        % XML_HOST
    )
    XML_URL_FOR = (
        "%s/air_quality/aqhi/{}/forecast/realtime/xml/AQ_FCST_{}_CURRENT.xml"
        % XML_HOST
    )

    conditions_meta = {
        "aqhi": {
            "xpath": "airQualityHealthIndex",
            "english": "Air Quality Health Index",
            "french": "Cote air santé",
        }
    }

    metadata_meta = {
        "timestamp": {"xpath": "dateStamp/UTCStamp", "xtype": "text"},
        "cgndb": {"xpath": "region", "xtype": "text"},
        "location": {"xpath": "region", "xtype": "attrib"},
    }

    """Get data from Environment Canada."""

    def __init__(
        self,
        zone_abreviation=None,
        region_cgndb=None,
        coordinates=None,
        language="english",
    ):
        """Initialize the data object."""
        # due to inconsistency in XML file attr naming
        self.language = language
        self.language_abr = language[:2].upper()
        self.zone_name_tag = 'name_%s_CA' % self.language_abr.lower()
        self.region_name_tag = 'name%s' % self.language_abr.title()
        # getting closest site
        if zone_abreviation and region_cgndb:
            self.abreviation = zone_abreviation
            self.region_cgndb = region_cgndb
            site = self.get_site(zone_abreviation, region_cgndb)
            self.site = site[0] if site else ''
        else:
            self.site = self.closest_site(coordinates[0], coordinates[1])
            self.abreviation = self.site["abreviation"]
            self.region_cgndb = self.site["cgndb"]
        self.metadata = {}
        self.conditions = {}
        self.forecast_time = ""
        self.daily_forecasts = []
        self.hourly_forecasts = []

        self.update()

    @ignore_ratelimit_exception
    @limits(calls=RATE_LIMIT_CALLS, period=RATE_LIMIT_PERIOD)
    def update(self):
        result = requests.get(
            self.XML_URL_OBS.format(self.abreviation, self.region_cgndb),
            timeout=10,
        )
        site_xml = result.content.decode("utf-8-sig")
        xml_object = et.fromstring(site_xml)

        # Update metadata
        for m, meta in self.metadata_meta.items():
            val = getattr(xml_object.find(meta["xpath"]), meta["xtype"])
            if isinstance(val, dict):
                for k, v in val.items():
                    self.metadata['%s_%s' % (m, k)] = v
            else:
                self.metadata[m] = val

        # Update current conditions
        def get_condition(meta):
            condition = {}

            element = xml_object.find(meta["xpath"])

            if element is not None:
                condition["value"] = element.text
            return condition

        for c, meta in self.conditions_meta.items():
            self.conditions[c] = {"label": meta[self.language]}
            self.conditions[c].update(get_condition(meta))

        # Update forecasts
        result = requests.get(
            self.XML_URL_FOR.format(self.abreviation, self.region_cgndb),
            timeout=10,
        )
        site_xml = result.content.decode("ISO-8859-1")
        xml_object = et.fromstring(site_xml)

        self.forecast_time = xml_object.findtext("./dateStamp/UTCStamp")

        # Update daily forecasts
        period = None
        for f in xml_object.findall("./forecastGroup/forecast"):
            for p in f.findall("./period"):
                if self.language_abr == p.attrib["lang"]:
                    period = p.attrib["forecastName"]
            self.daily_forecasts.append(
                {
                    "period": period,
                    "aqhi": f.findtext("./airQualityHealthIndex"),
                }
            )

        # Update hourly forecasts
        for f in xml_object.findall("./hourlyForecastGroup/hourlyForecast"):
            self.hourly_forecasts.append(
                {"period": f.attrib["UTCTime"], "aqhi": f.text}
            )

    def get_regions(self):
        """Get list of all sites from Environment Canada, for auto-config."""
        result = requests.get(self.XML_LIST_URL, timeout=10)
        site_xml = result.content.decode("utf-8-sig")
        xml_object = et.fromstring(site_xml)

        regions = []
        for zone in xml_object.findall("./EC_administrativeZone"):
            _zone_attribs = zone.attrib
            _zone_attrib = {
                "abreviation": _zone_attribs["abreviation"],
                "zone_name": _zone_attribs[self.zone_name_tag],
            }
            for region in zone.findall("./regionList/region"):
                _region_attribs = region.attrib

                _region_attrib = {
                    "region_name": _region_attribs[self.region_name_tag],
                    "cgndb": _region_attribs["cgndb"],
                }
                _region_attrib["latitude"] = float(_region_attribs["latitude"])
                _region_attrib["longitude"] = float(
                    _region_attribs["longitude"]
                )
                _children = region.getchildren()
                for child in _children:
                    _region_attrib[child.tag] = child.text
                _region_attrib.update(_zone_attrib)
                regions.append(_region_attrib)
        return regions

    def closest_site(self, lat, lon):
        """
        Return the region obj with the closest station to our lat/lon."""
        region_list = self.get_regions()

        def site_distance(site):
            """Calculate distance to a region."""
            return distance.distance(
                (lat, lon), (site["latitude"], site["longitude"])
            )

        closest = min(region_list, key=site_distance)

        return closest

    def get_site(self, zone, region):
        region_list = self.get_regions()

        def filter_region_zone(site):
            return site['abreviation'] == zone and site['cgndb'] == region

        region_obj = list(filter(filter_region_zone, region_list))
        return region_obj
