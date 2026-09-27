import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import voluptuous as vol
from aiohttp import ClientError, ClientSession
from geopy import distance
from lxml import etree as et

from .constants import CLIENT_TIMEOUT, USER_AGENT
from .ec_validate import coordinates

AQHI_SITE_LIST_URL = (
    "https://dd.weather.gc.ca/today/air_quality/doc/AQHI_XML_File_List.xml"
)
AQHI_OBSERVATION_URL = "https://dd.weather.gc.ca/today/air_quality/aqhi/{}/observation/realtime/xml/AQ_OBS_{}_CURRENT.xml"
AQHI_FORECAST_URL = "https://dd.weather.gc.ca/today/air_quality/aqhi/{}/forecast/realtime/xml/AQ_FCST_{}_CURRENT.xml"

LOG = logging.getLogger(__name__)

ATTRIBUTION = {
    "EN": "Data provided by Environment Canada",
    "FR": "Données fournies par Environnement Canada",
}


@dataclass
class MetaData:
    attribution: str
    timestamp: datetime | None = None
    location: str | None = None

    # Not used; needed for compatability with ec_weather metadata
    station: str | None = None


__all__ = ["ECAirQuality"]


def timestamp_to_datetime(timestamp):
    dt = datetime.strptime(timestamp, "%Y%m%d%H%M%S")
    dt = dt.replace(tzinfo=UTC)
    return dt


async def get_aqhi_regions(language):
    """Get list of all AQHI regions from Environment Canada, for auto-config."""
    zone_name_tag = f"name_{language.lower()}_CA"
    region_name_tag = f"name{language.title()}"

    LOG.debug("get_aqhi_regions() started")

    regions = []
    async with ClientSession(raise_for_status=True) as session:
        response = await session.get(
            AQHI_SITE_LIST_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=CLIENT_TIMEOUT,
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
                    vol.Optional("coordinates"): coordinates,
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

        self.metadata = MetaData(ATTRIBUTION[self.language])
        self.region_name = None
        self.current = None
        self.current_timestamp = None
        self.forecasts = dict(daily={}, hourly={})

    async def get_aqhi_data(self, url):
        """Fetch and parse one AQHI document, or None if it can't be had."""
        try:
            async with ClientSession(raise_for_status=True) as session:
                response = await session.get(
                    url.format(self.zone_id, self.region_id),
                    headers={"User-Agent": USER_AGENT},
                    timeout=CLIENT_TIMEOUT,
                )
                body = await response.read()
        except (ClientError, TimeoutError):
            LOG.debug("Retrieving AQHI failed", exc_info=True)
            return None

        # An unreadable body - truncated, or an error page - is handled like
        # a failed request. lxml's XMLSyntaxError is not the xml.etree
        # ParseError that callers such as Home Assistant catch.
        try:
            return et.fromstring(body)
        except et.XMLSyntaxError as err:
            LOG.warning("Unreadable AQHI response from %s: %s", url, err)
            return None

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
            region = aqhi_current.find("region")
            if region is not None:
                self.region_name = region.get(f"name{self.language.title()}")
                self.metadata.location = self.region_name

            # An element that is missing or empty means "no value".
            index = aqhi_current.findtext("airQualityHealthIndex")
            self.current = float(index) if index else None

            stamp = aqhi_current.findtext("./dateStamp/UTCStamp")
            self.current_timestamp = timestamp_to_datetime(stamp) if stamp else None
            self.metadata.timestamp = self.current_timestamp
            LOG.debug(
                "update(): aqhi_current %s timestamp %s",
                self.current,
                self.current_timestamp,
            )

        # Update AQHI forecasts
        aqhi_forecast = await self.get_aqhi_data(url=AQHI_FORECAST_URL)

        # Each forecast replaces the last rather than being added to it.
        if aqhi_forecast is not None:
            daily = {}
            for f in aqhi_forecast.findall("./forecastGroup/forecast"):
                period = next(
                    (
                        p.get("forecastName")
                        for p in f.findall("./period")
                        if p.get("lang") == self.language
                    ),
                    None,
                )
                if period is None:
                    continue
                daily[period] = int(f.findtext("./airQualityHealthIndex") or 0)

            hourly = {
                timestamp_to_datetime(f.attrib["UTCTime"]): int(f.text or 0)
                for f in aqhi_forecast.findall("./hourlyForecastGroup/hourlyForecast")
            }

            self.forecasts["daily"] = daily
            self.forecasts["hourly"] = hourly
