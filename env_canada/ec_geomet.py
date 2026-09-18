"""Shared plumbing for Environment Canada's GeoMet WMS server.

Both the map/radar imagery (`ec_map`) and the point-value precipitation
series (`ec_precip_forecast`) talk to the same WMS endpoint, so the HTTP
call, the bounding-box maths and the GetCapabilities dimension parsing all
live here.
"""

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import dateutil.parser
from aiohttp import ClientSession
from lxml import etree as et

from .constants import USER_AGENT
from .ec_cache import Cache

LOG = logging.getLogger(__name__)

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

# How long a GetCapabilities response stays cached. The server publishes one
# new step per time dimension, sliding a fixed-width window forward, so the
# response is worth re-reading about once per step - often enough to pick up
# the newest frame promptly, rarely enough not to re-fetch a 20 kB document
# on every poll. The bounds keep a layer with a very short or very long step
# (HRDPS advertises PT1H) within reason, and the fallback covers a dimension
# that advertises no step at all.
MIN_CAPABILITIES_CACHE_TIME = timedelta(minutes=1)
MAX_CAPABILITIES_CACHE_TIME = timedelta(minutes=15)
DEFAULT_CAPABILITIES_CACHE_TIME = timedelta(minutes=5)


@dataclass
class LayerDimension:
    """A WMS layer's dimension, as advertised by GetCapabilities."""

    start: datetime
    end: datetime
    default: str | None
    step: timedelta | None
    # When the capabilities response this came from was read. Defaults to
    # now, which is what a directly constructed dimension means.
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def _grid_index(self, moment: datetime) -> int:
        """Which step of this dimension's own grid `moment` falls in."""
        assert self.step  # callers check; keeps the arithmetic honest
        return (moment - self.start) // self.step

    @property
    def effective_start(self) -> datetime:
        """`start`, moved forward by however far the window may have slid
        since this response was read.

        GeoMet advertises a dimension as a range - "start/end/step" - and
        not as a list of the timestamps it holds, so the range is all a
        caller has to go on. For the radar layers it is a fixed-width window
        that slides: publishing a new step drops the oldest one, advancing
        both ends together. A response held in the cache across a
        publication therefore names a `start` the server no longer serves,
        and asking for it returns a ServiceExceptionReport (XML, HTTP 200)
        rather than an image.

        Publications land on the dimension's own grid, so the count of grid
        instants crossed since the response was read bounds how many times
        the window can have moved on. It is only a bound: a step is
        published a little after the instant it is stamped with, so this can
        give up the oldest frame a minute or two before it actually
        expires - cheaper than spending a request to be told it is gone, and
        the frame is one of ~31.

        `end` needs no such treatment. It only ever grows stale towards the
        past, which stays inside the window.
        """
        if not self.step:
            return self.start
        crossings = self._grid_index(datetime.now(UTC)) - self._grid_index(
            self.fetched_at
        )
        return min(self.start + max(0, crossings) * self.step, self.end)


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


def _parse_capabilities(capabilities_xml, layer_name):
    """Parse a GetCapabilities document, or None if it can't be read.

    A response that isn't XML at all - a proxy error page, a truncated
    body - would otherwise raise out of whichever update() asked for it,
    taking down everything that call was building.
    """
    try:
        return et.fromstring(capabilities_xml)
    except et.XMLSyntaxError as err:
        LOG.warning("Unreadable GetCapabilities response for %s: %s", layer_name, err)
        return None


def _parse_dimension(root, layer_name, dimension):
    """Pull one dimension out of a parsed GetCapabilities document.

    Returns (start, end, default, step), or None if the layer or dimension
    isn't there.
    """
    element = root.find(
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
    return start, end, element.get("default"), step


def _capabilities_cache_time(root, layer_name) -> timedelta:
    """How long to hold a GetCapabilities response, from the cadence the
    layer's time dimension advertises."""
    parsed = _parse_dimension(root, layer_name, "time")
    step = parsed[3] if parsed else None
    if not step:
        return DEFAULT_CAPABILITIES_CACHE_TIME
    return max(MIN_CAPABILITIES_CACHE_TIME, min(step, MAX_CAPABILITIES_CACHE_TIME))


async def get_layer_dimension(
    layer_name, dimension="time", fetch=get_resource
) -> LayerDimension | None:
    """Fetch a WMS layer's dimension from GetCapabilities.

    Returns a LayerDimension, or None if the layer or dimension doesn't exist.
    `fetch` lets a caller route the HTTP call through its own module-level
    function so that existing patch targets keep working.
    """

    capabilities_cache_key = f"capabilities-{layer_name}"

    if cached := Cache.get(capabilities_cache_key):
        fetched_at, capabilities_xml = cached
    else:
        params = {**capabilities_params, "layer": layer_name}
        capabilities_xml = await fetch(geomet_url, params, bytes=True)
        # When the response was read, so a caller can tell how far the
        # window may have slid since - see LayerDimension.effective_start.
        fetched_at = datetime.now(UTC)

    root = _parse_capabilities(capabilities_xml, layer_name)
    if root is None:
        # Not cached: an unreadable response is worth asking again for on
        # the next poll rather than holding on to.
        return None

    if not cached:
        Cache.add(
            capabilities_cache_key,
            (fetched_at, capabilities_xml),
            _capabilities_cache_time(root, layer_name),
        )

    parsed = _parse_dimension(root, layer_name, dimension)
    if parsed is None:
        return None

    start, end, default, step = parsed
    return LayerDimension(
        start=start, end=end, default=default, step=step, fetched_at=fetched_at
    )
