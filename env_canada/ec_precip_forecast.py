"""Precipitation forecast series assembled from Environment Canada's GeoMet
WMS server via GetFeatureInfo point queries.

Two independent tracks are produced, together covering the same ground as a
typical weather app's precipitation histogram:

* `nowcast` - 6-minute precipitation rate, from observed radar for the recent
  past and from the radar extrapolation (nowcast) layers for roughly the next
  hour.
* `hourly` - hourly precipitation amount, probability and type for the next
  two days, from the HRDPS model and its "Weather Elements on Grid" (WEonG)
  diagnostic suite.

This class only gathers and assembles the data; rendering is left to the
caller.
"""

import asyncio
import json
import logging
import math
from datetime import date, datetime, timedelta

import voluptuous as vol
from aiohttp.client_exceptions import ClientError

from .ec_cache import Cache
from .ec_geomet import (
    ATTRIBUTION,
    TIME_FORMAT,
    LayerDimension,
    geomet_url,
    get_layer_dimension,
)
from .ec_geomet import get_resource as _get_resource
from .ec_validate import coordinates

LOG = logging.getLogger(__name__)

__all__ = ["ECPrecipForecast"]

# Radar layers, keyed by precipitation type. The observed and extrapolation
# layers share a units convention: rain in mm/h, snow in cm/h.
nowcast_layers = {
    "rain": {
        "observed": "RADAR_1KM_RRAI",
        "forecast": "Radar_1km_RainPrecipRate-Extrapolation",
        "unit": "mm/h",
    },
    "snow": {
        "observed": "RADAR_1KM_RSNO",
        "forecast": "Radar_1km_SnowPrecipRate-Extrapolation",
        "unit": "cm/h",
    },
}

# Radar-derived surface precipitation type, used to resolve precip_type="auto".
# There is no extrapolation counterpart, so it only describes the present.
precip_type_layer = "Radar_1km_SfcPrecipType"

# HRDPS precipitation accumulation. Values are cumulative from the start of
# the model run, so per-hour amounts come from differencing adjacent steps.
hourly_amount_layer = "HRDPS.CONTINENTAL_PR"

# HRDPS "Weather Elements on Grid" diagnostics, all hourly.
hourly_layers = {
    "probability": "HRDPS-WEonG_2.5km_Precip-Prob",
    "conditional_amount": "HRDPS-WEonG_2.5km_PrecipCondAmt",
    "precip_type": "HRDPS-WEonG_2.5km_DominantPrecipType",
}

feature_info_params = {
    "service": "WMS",
    "version": "1.3.0",
    "request": "GetFeatureInfo",
    "crs": "EPSG:4326",
    "info_format": "application/json",
    "width": "100",
    "height": "100",
    "i": "50",
    "j": "50",
}

# GetFeatureInfo needs a bounding box and a pixel within it rather than a bare
# coordinate. A small box centred on the point puts the queried pixel well
# inside the finest grid cell any of these layers uses (1 km radar).
point_delta = 0.01

# Fallbacks for the rare case where GetCapabilities omits a dimension's step.
default_nowcast_step = timedelta(minutes=6)
default_hourly_step = timedelta(hours=1)

# The radar layers refresh every 6 minutes; the HRDPS runs every 6 hours, so
# its values are stable for far longer.
nowcast_cache_time = timedelta(minutes=5)
hourly_cache_time = timedelta(hours=3)

# GeoMet answers a point query in roughly 0.3 s, so a modest amount of
# concurrency turns a ~100-request update into a few seconds without
# hammering the server.
max_concurrent_requests = 8

# `class` labels the server uses for the radar precipitation type, mapped onto
# the two radar rate layers.
snow_type_keywords = ("snow", "ice", "hail", "pellet")
rain_type_keywords = ("rain", "drizzle")


class ECPrecipForecast:
    def __init__(self, **kwargs):
        """Initialize the precipitation forecast object."""

        init_schema = vol.Schema(
            {
                vol.Required("coordinates"): coordinates,
                vol.Required("precip_type", default="auto"): vol.In(
                    ["auto", "rain", "snow"]
                ),
                vol.Required("past_minutes", default=60): vol.All(
                    int, vol.Range(0, 180)
                ),
                vol.Required("future_minutes", default=72): vol.All(
                    int, vol.Range(0, 120)
                ),
                vol.Required("hourly_hours", default=24): vol.All(
                    int, vol.Range(0, 48)
                ),
                vol.Optional("language", default="english"): vol.In(
                    ["english", "french"]
                ),
            }
        )

        kwargs = init_schema(kwargs)

        self.coordinates = kwargs["coordinates"]
        self.language = kwargs["language"]
        self.precip_type = kwargs["precip_type"]
        self.past_minutes = kwargs["past_minutes"]
        self.future_minutes = kwargs["future_minutes"]
        self.hourly_hours = kwargs["hourly_hours"]

        latitude, longitude = self.coordinates
        self.bbox = (
            round(latitude - point_delta, 5),
            round(longitude - point_delta, 5),
            round(latitude + point_delta, 5),
            round(longitude + point_delta, 5),
        )

        self.nowcast: list[dict] = []
        self.hourly: list[dict] = []
        self.metadata: dict = {"attribution": ATTRIBUTION[self.language]}

        self._semaphore: asyncio.Semaphore | None = None

    def _get_cache_prefix(self):
        """Generate a location-specific cache prefix based on bounding box."""
        return f"{self.bbox[0]:.3f},{self.bbox[1]:.3f}"

    def clear_cache(self) -> int:
        """Clear all cached data for this location.

        Returns:
            Number of cache entries cleared.
        """
        count = Cache.clear(self._get_cache_prefix())
        for layer in (
            *(layer for entry in nowcast_layers.values() for layer in entry.values()),
            precip_type_layer,
            hourly_amount_layer,
            *hourly_layers.values(),
        ):
            count += Cache.clear(f"capabilities-{layer}")
        return count

    async def _get_feature_info(
        self, layer, time, reference_time=None, cache_time=None
    ):
        """Query a single layer for its value at this location and time.

        Returns the feature's `properties` dict, or None when the server has
        no data for that combination.
        """
        formatted_time = time.strftime(TIME_FORMAT)
        cache_key = (
            f"{self._get_cache_prefix()}-gfi-{layer}-{formatted_time}-{reference_time}"
        )
        if (cached := Cache.get(cache_key)) is not None:
            return cached

        params = {
            **feature_info_params,
            "lang": "en",
            "layers": layer,
            "query_layers": layer,
            "bbox": ",".join(str(coord) for coord in self.bbox),
            "time": formatted_time,
        }
        if reference_time:
            params["dim_reference_time"] = reference_time

        semaphore = self._semaphore
        if semaphore is None:
            semaphore = self._semaphore = asyncio.Semaphore(max_concurrent_requests)

        try:
            async with semaphore:
                body = await _get_resource(geomet_url, params, bytes=False)
        except ClientError as err:
            LOG.warning("Could not query %s at %s: %s", layer, formatted_time, err)
            return None

        # GetCapabilities advertises a continuous time range but doesn't
        # guarantee every step within it has data. A miss comes back as a
        # ServiceExceptionReport - XML, with HTTP 200 - rather than an error
        # status, so it has to be detected from the body.
        if body.lstrip().startswith("<"):
            LOG.warning("No data for %s at %s", layer, formatted_time)
            return None

        try:
            features = json.loads(body).get("features") or []
        except json.JSONDecodeError:
            LOG.warning("Unparsable response for %s at %s", layer, formatted_time)
            return None

        if not features:
            # A layer with nothing to report at this point - for instance
            # no precipitation over a clear sky - answers with an empty
            # collection.
            LOG.debug("No feature returned for %s at %s", layer, formatted_time)
            return None

        properties = features[0].get("properties", {})
        return Cache.add(cache_key, properties, cache_time or nowcast_cache_time)

    async def _dimension(self, layer, dimension="time") -> LayerDimension | None:
        """Fetch a layer's dimension, logging rather than raising on absence."""
        result = await get_layer_dimension(layer, dimension, fetch=_get_resource)
        if result is None:
            LOG.warning("Layer %s advertises no %s dimension", layer, dimension)
        return result

    @staticmethod
    def _steps(dimension, earliest, latest, fallback_step):
        """Times on a dimension's own grid that fall within [earliest, latest].

        GeoMet rejects any time that isn't exactly on the advertised step
        (dimensions carry nearestValue="0"), so times are generated from the
        dimension's start rather than from the clock.
        """
        step = dimension.step or fallback_step
        last_index = math.floor((dimension.end - dimension.start) / step)
        first = max(0, math.ceil((earliest - dimension.start) / step))
        last = min(last_index, math.floor((latest - dimension.start) / step))
        return [dimension.start + index * step for index in range(first, last + 1)]

    def _seasonal_precip_type(self):
        """Fall back to the season when radar reports no precipitation."""
        return "rain" if date.today().month in range(4, 11) else "snow"

    async def _resolve_precip_type(self):
        """Decide which radar rate layer to read.

        Returns (precip_type, properties) where properties is the surface
        precipitation type observation used to decide, if there was one.
        """
        if self.precip_type in ("rain", "snow"):
            return self.precip_type, None

        dimension = await self._dimension(precip_type_layer)
        if dimension is None:
            return self._seasonal_precip_type(), None

        properties = await self._get_feature_info(precip_type_layer, dimension.end)
        label = (properties or {}).get("class") or ""
        lowered = label.lower()
        if any(keyword in lowered for keyword in snow_type_keywords):
            return "snow", properties
        if any(keyword in lowered for keyword in rain_type_keywords):
            return "rain", properties
        return self._seasonal_precip_type(), properties

    async def _update_nowcast(self):
        """Assemble the 6-minute precipitation rate series."""
        self.nowcast = []

        precip_type, type_properties = await self._resolve_precip_type()
        layers = nowcast_layers[precip_type]
        unit = layers["unit"]

        observed = await self._dimension(layers["observed"])
        if observed is None:
            return

        self.metadata["timestamp"] = observed.end.isoformat()
        if type_properties:
            self.metadata["precip_type"] = type_properties.get("class")
        self.metadata["precip_type_resolved"] = precip_type

        requests = []
        if self.past_minutes:
            for time in self._steps(
                observed,
                observed.end - timedelta(minutes=self.past_minutes),
                observed.end,
                default_nowcast_step,
            ):
                requests.append((layers["observed"], time, None, False))

        if self.future_minutes:
            forecast = await self._dimension(layers["forecast"])
            if forecast is not None:
                # Pin every forecast step to one model run so the series
                # doesn't mix runs if a new one lands mid-update.
                reference = await self._dimension(layers["forecast"], "reference_time")
                reference_time = reference.default if reference else None
                self.metadata["nowcast_reference_time"] = reference_time

                # The observed and extrapolation layers refresh on independent
                # schedules, so the extrapolation window can open before or
                # after the last observation. Steps are taken from the
                # extrapolation layer's own grid: where its window opens late,
                # the gap is left empty rather than filled with a value that
                # belongs to a different time.
                for time in self._steps(
                    forecast,
                    observed.end + timedelta(seconds=1),
                    observed.end + timedelta(minutes=self.future_minutes),
                    default_nowcast_step,
                ):
                    requests.append((layers["forecast"], time, reference_time, True))

        results = await asyncio.gather(
            *(
                self._get_feature_info(layer, time, reference_time)
                for layer, time, reference_time, _ in requests
            )
        )

        for (_, time, _, is_forecast), properties in zip(
            requests, results, strict=True
        ):
            if properties is None:
                continue
            rate = properties.get("value")
            if rate is None:
                continue
            self.nowcast.append(
                {
                    "timestamp": time,
                    "rate": float(rate),
                    "unit": unit,
                    "label": properties.get("class"),
                    "precip_type": precip_type,
                    "forecast": is_forecast,
                }
            )

        self.nowcast.sort(key=lambda entry: entry["timestamp"])

    async def _update_hourly(self):
        """Assemble the hourly amount/probability/type series.

        Each entry describes the hour *ending* at its timestamp, matching how
        the model reports accumulation.
        """
        self.hourly = []

        amount_dimension = await self._dimension(hourly_amount_layer)
        if amount_dimension is None:
            return

        reference = await self._dimension(hourly_amount_layer, "reference_time")
        reference_time = reference.default if reference else None
        self.metadata["hourly_reference_time"] = reference_time

        anchor = (
            datetime.fromisoformat(amount_dimension.default.replace("Z", "+00:00"))
            if amount_dimension.default
            else amount_dimension.start
        )
        times = self._steps(
            amount_dimension,
            anchor,
            anchor + timedelta(hours=self.hourly_hours),
            default_hourly_step,
        )
        if not times:
            return

        step = amount_dimension.step or default_hourly_step

        # Accumulation is cumulative from the start of the run, so each hour's
        # amount is a difference. The step before the first one is needed as a
        # baseline; where it falls outside the layer's window the first
        # published step is itself the first hour's accumulation, so the
        # baseline is zero.
        baseline_time = times[0] - step
        amount_times = (
            [baseline_time, *times]
            if baseline_time >= amount_dimension.start
            else list(times)
        )

        amount_results, *diagnostic_results = await asyncio.gather(
            asyncio.gather(
                *(
                    self._get_feature_info(
                        hourly_amount_layer, time, reference_time, hourly_cache_time
                    )
                    for time in amount_times
                )
            ),
            *(
                asyncio.gather(
                    *(
                        self._get_feature_info(
                            layer, time, reference_time, hourly_cache_time
                        )
                        for time in times
                    )
                )
                for layer in hourly_layers.values()
            ),
        )

        amounts = dict(zip(amount_times, amount_results, strict=True))
        diagnostics = {
            key: dict(zip(times, results, strict=True))
            for key, results in zip(hourly_layers, diagnostic_results, strict=True)
        }

        for time in times:
            current = _value(amounts.get(time))
            if current is None:
                continue
            previous = _value(amounts.get(time - step))
            if previous is None:
                previous = 0.0
            # Successive steps of a cumulative field can differ by a hair in
            # the wrong direction purely from storage precision.
            amount = round(max(current - previous, 0.0), 3)

            probability = _value(diagnostics["probability"].get(time))
            conditional_properties = diagnostics["conditional_amount"].get(time)
            conditional_amount = _value(conditional_properties)
            if conditional_amount is not None:
                # The layer is published in metres.
                conditional_amount = round(conditional_amount * 1000, 3)

            expected_amount = None
            if probability is not None and conditional_amount is not None:
                expected_amount = round(probability / 100 * conditional_amount, 3)

            self.hourly.append(
                {
                    "timestamp": time,
                    "amount": amount,
                    "probability": int(probability)
                    if probability is not None
                    else None,
                    "conditional_amount": conditional_amount,
                    "expected_amount": expected_amount,
                    "precip_type": (diagnostics["precip_type"].get(time) or {}).get(
                        "class"
                    ),
                    "label": (conditional_properties or {}).get("class"),
                }
            )

    async def update(self):
        """Fetch both precipitation series."""
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)

        tracks = []
        if self.past_minutes or self.future_minutes:
            tracks.append(self._update_nowcast())
        if self.hourly_hours:
            tracks.append(self._update_hourly())
        await asyncio.gather(*tracks)


def _value(properties):
    """Pull a numeric value out of a GetFeatureInfo properties dict."""
    if not properties:
        return None
    value = properties.get("value")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
