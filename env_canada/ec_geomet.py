"""Shared plumbing for Environment Canada's GeoMet WMS server.

Both the map/radar imagery (`ec_map`) and the point-value precipitation
series (`ec_precip_forecast`) talk to the same WMS endpoint, so the HTTP
call, the bounding-box maths and the GetCapabilities dimension parsing all
live here.
"""

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import dateutil.parser
from aiohttp import ClientSession
from lxml import etree as et

from .constants import USER_AGENT
from .ec_cache import Cache

ATTRIBUTION = {
    "english": "Data provided by Environment Canada",
    "french": "Données fournies par Environnement Canada",
}

__all__ = [
    "ATTRIBUTION",
    "LayerDimension",
    "compute_bounding_box",
    "get_layer_dimension",
    "get_resource",
]

geomet_url = "https://geo.weather.gc.ca/geomet"
capabilities_params = {
    "lang": "en",
    "service": "WMS",
    "version": "1.3.0",
    "request": "GetCapabilities",
}
wms_namespace = {"wms": "http://www.opengis.net/wms"}
dimension_xpath = './/wms:Layer[wms:Name="{layer}"]/wms:Dimension[@name="{dim}"]'

# GeoMet serves times as "%Y-%m-%dT%H:%M:%SZ" and rejects anything else,
# including times that don't land exactly on a dimension's step.
TIME_FORMAT = "%Y-%m-%dT%H:%M:00Z"

_duration_re = re.compile(
    r"^P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


@dataclass
class LayerDimension:
    """A WMS layer's dimension, as advertised by GetCapabilities."""

    start: datetime
    end: datetime
    default: str | None
    step: timedelta | None


def parse_iso_duration(duration: str) -> timedelta | None:
    """Parse the ISO 8601 duration in a WMS dimension (e.g. "PT6M", "PT1H").

    Only the subset GeoMet actually uses is supported; anything else (notably
    the year/month designators, which aren't fixed-length) returns None.
    """
    match = _duration_re.match(duration.strip())
    if not match:
        return None
    parts = {key: int(value) for key, value in match.groupdict().items() if value}
    if not parts:
        return None
    return timedelta(**parts)


def compute_bounding_box(distance, latittude, longitude):
    """
    Modified from https://gist.github.com/alexcpn/f95ae83a7ee0293a5225
    """
    latittude = math.radians(latittude)
    longitude = math.radians(longitude)

    distance_from_point_km = distance
    angular_distance = distance_from_point_km / 6371.01

    lat_min = max(-math.pi / 2, latittude - angular_distance)
    lat_max = min(math.pi / 2, latittude + angular_distance)

    cos_latittude = math.cos(latittude)
    ratio = math.sin(angular_distance) / cos_latittude if cos_latittude else math.inf

    if abs(ratio) >= 1:
        # Circle encloses a pole: longitude spans the full range.
        lon_min = -math.pi
        lon_max = math.pi
    else:
        delta_longitude = math.asin(ratio)
        lon_min = longitude - delta_longitude
        lon_max = longitude + delta_longitude
    lon_min = round(math.degrees(lon_min), 5)
    lat_max = round(math.degrees(lat_max), 5)
    lon_max = round(math.degrees(lon_max), 5)
    lat_min = round(math.degrees(lat_min), 5)

    return lat_min, lon_min, lat_max, lon_max


async def get_resource(url, params, bytes=True):
    async with ClientSession(raise_for_status=True) as session:
        response = await session.get(
            url=url, params=params, headers={"User-Agent": USER_AGENT}
        )
        if bytes:
            return await response.read()
        return await response.text()


async def get_layer_dimension(
    layer_name, dimension="time", fetch=get_resource
) -> LayerDimension | None:
    """Fetch a WMS layer's dimension from GetCapabilities.

    Returns a LayerDimension, or None if the layer or dimension doesn't exist.
    `fetch` lets a caller route the HTTP call through its own module-level
    function so that existing patch targets keep working.
    """

    capabilities_cache_key = f"capabilities-{layer_name}"

    if not (capabilities_xml := Cache.get(capabilities_cache_key)):
        params = {**capabilities_params, "layer": layer_name}
        capabilities_xml = await fetch(geomet_url, params, bytes=True)
        Cache.add(capabilities_cache_key, capabilities_xml, timedelta(minutes=5))

    element = et.fromstring(capabilities_xml).find(
        dimension_xpath.format(layer=layer_name, dim=dimension),
        namespaces=wms_namespace,
    )
    if element is None or not element.text:
        return None

    # Dimension text is "start/end/step", e.g.
    # "2026-09-13T11:30:00Z/2026-09-13T14:30:00Z/PT6M".
    values = element.text.split("/")
    if len(values) < 2:
        return None

    start, end = (dateutil.parser.isoparse(t) for t in values[:2])
    step = parse_iso_duration(values[2]) if len(values) > 2 else None
    return LayerDimension(
        start=start, end=end, default=element.get("default"), step=step
    )
