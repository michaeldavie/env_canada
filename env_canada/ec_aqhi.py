import logging
from datetime import datetime, timezone

import voluptuous as vol
from aiohttp import ClientSession
from geopy import distance
from lxml import etree as et

from .constants import USER_AGENT

AQHI_SITE_LIST_URL = "https://dd.weather.gc.ca/air_quality/doc/AQHI_XML_File_List.xml"
AQHI_OBSERVATION_URL = "https://dd.weather.gc.ca/air_quality/aqhi/{}/observation/realtime/xml/AQ_OBS_{}_CURRENT.xml"
AQHI_FORECAST_URL = "https://dd.weather.gc.ca/air_quality/aqhi/{}/forecast/realtime/xml/AQ_FCST_{}_CURRENT.xml"

LOG = logging.getLogger(__name__)

ATTRIBUTION = {
    "EN": "Data provided by Environment Canada",
    "FR": "Données fournies par Environnement Canada",
}


__all__ = ["ECAirQuality"]


def timestamp_to_datetime(timestamp):
    dt = datetime.strptime(timestamp, "%Y%m%d%H%M%S")
    dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def get_aqhi_regions(language):
    """Get list of all AQHI regions from Environment Canada, for auto-config."""
    zone_name_tag = "name_%s_CA" % language.lower()
    region_name_tag = "name%s" % language.title()

    LOG.debug("get_aqhi_regions() started")

    regions = []
    async with ClientSession(raise_for_status=True) as session:
        response = await session.get(
            AQHI_SITE_LIST_URL, headers={"User-Agent": USER_AGENT}, timeout=10
        )
        result = await response.read()

    site_xml = result
    xml_object = et.fromstring(site_xml)

    for zone in xml_object.findall("./EC_administrativeZone"):
        _zone_attribs = zone.attrib
        _zone_attrib = {
            "abbreviation": _zone_attribs["abreviation"],
            "zone_name": _zone_attribs[zone_name_tag],
        }
        for region in zone.findall("./regionList/region"):
            _region_attribs = region.attrib

            _region_attrib = {
                "region_name": _region_attribs[region_name_tag],
                "cgndb": _region_attribs["cgndb"],
                "latitude": float(_region_attribs["latitude"]),
                "longitude": float(_region_attribs["longitude"]),
            }
            _children = list(region)
            for child in _children:
                _region_attrib[child.tag] = child.text
            _region_attrib.update(_zone_attrib)
            regions.append(_region_attrib)

    LOG.debug("get_aqhi_regions(): found %d regions", len(regions))

    return regions


async def find_closest_region(language, lat, lon):
    """Return the AQHI region and site ID of the closest site."""
    region_list = await get_aqhi_regions(language)

    def site_distance(site):
        """Calculate distance to a region."""
        return distance.distance((lat, lon), (site["latitude"], site["longitude"]))

    return min(region_list, key=site_distance)


class ECAirQuality:
    """Get air quality data from Environment Canada."""

    def __init__(self, **kwargs):
        """Initialize the data object."""

        init_schema = vol.Schema(
            vol.All(
                vol.Any(
                    {
                        vol.Required("coordinates"): object,
                        vol.Optional("language"): object,
                    },
                    {
                        vol.Required("zone_id"): object,
                        vol.Required("region_id"): object,
                        vol.Optional("language"): object,
                    },
                ),
                {
                    vol.Optional("zone_id"): vol.In(
                        ["atl", "ont", "pnr", "pyr", "que"]
                    ),
                    vol.Optional("region_id"): vol.All(str, vol.Length(5)),
                    vol.Optional("coordinates"): (
                        vol.All(vol.Or(int, float), vol.Range(-90, 90)),
                        vol.All(vol.Or(int, float), vol.Range(-180, 180)),
                    ),
                    vol.Optional("language", default="EN"): vol.In(["EN", "FR"]),
                },
            )
        )

        kwargs = init_schema(kwargs)

        self.language = kwargs["language"]

        if (
            "zone_id" in kwargs
            and "region_id" in kwargs
            and kwargs["zone_id"] is not None
            and kwargs["region_id"] is not None
        ):
            self.zone_id = kwargs["zone_id"]
            self.region_id = kwargs["region_id"].upper()
        else:
            self.zone_id = None
            self.region_id = None
            self.coordinates = kwargs["coordinates"]

        self.metadata = {"attribution": ATTRIBUTION[self.language]}
        self.region_name = None
        self.current = None
        self.current_timestamp = None
        self.forecasts = dict(daily={}, hourly={})

    async def get_aqhi_data(self, url):
        async with ClientSession(raise_for_status=True) as session:
            try:
                response = await session.get(
                    url.format(self.zone_id, self.region_id),
                    headers={"User-Agent": USER_AGENT},
                    timeout=10,
                )
            except Exception:
                LOG.debug("Retrieving AQHI failed", exc_info=True)
                return None

            result = await response.read()
            aqhi_xml = result
            return et.fromstring(aqhi_xml)

    async def update(self):
        # Find closest site if not identified

        if not (self.zone_id and self.region_id):
            closest = await find_closest_region(self.language, *self.coordinates)
            self.zone_id = closest["abbreviation"]
            self.region_id = closest["cgndb"]
            LOG.debug(
                "update() closest region returned: zone_id '%s' region_id '%s'",
                self.zone_id,
                self.region_id,
            )

        # Fetch current measurement
        aqhi_current = await self.get_aqhi_data(url=AQHI_OBSERVATION_URL)

        if aqhi_current is not None:
            # Update region name
            element = aqhi_current.find("region")
            self.region_name = element.attrib[
                "name{lang}".format(lang=self.language.title())
            ]
            self.metadata["location"] = self.region_name

            # Update AQHI current condition
            element = aqhi_current.find("airQualityHealthIndex")
            if element is not None:
                self.current = float(element.text)
            else:
                self.current = None

            element = aqhi_current.find("./dateStamp/UTCStamp")
            if element is not None:
                self.current_timestamp = timestamp_to_datetime(element.text)
            else:
                self.current_timestamp = None
            self.metadata["timestamp"] = self.current_timestamp
            LOG.debug(
                "update(): aqhi_current %d timestamp %s",
                self.current,
                self.current_timestamp,
            )

        # Update AQHI forecasts
        aqhi_forecast = await self.get_aqhi_data(url=AQHI_FORECAST_URL)

        if aqhi_forecast is not None:
            # Update AQHI daily forecasts
            for f in aqhi_forecast.findall("./forecastGroup/forecast"):
                for p in f.findall("./period"):
                    if self.language == p.attrib["lang"]:
                        period = p.attrib["forecastName"]
                self.forecasts["daily"][period] = int(
                    f.findtext("./airQualityHealthIndex") or 0
                )

            # Update AQHI hourly forecasts
            for f in aqhi_forecast.findall("./hourlyForecastGroup/hourlyForecast"):
                self.forecasts["hourly"][timestamp_to_datetime(f.attrib["UTCTime"])] = (
                    int(f.text or 0)
                )
