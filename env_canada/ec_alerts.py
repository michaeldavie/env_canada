import copy
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aiohttp import ClientSession, ClientTimeout

from .constants import USER_AGENT
from .ec_cache import Cache

GEOMET_WFS_URL = "https://geo.weather.gc.ca/geomet"

ALERTS_WFS_PARAMS = {
    "SERVICE": "WFS",
    "VERSION": "2.0.0",
    "REQUEST": "GetFeature",
    "TYPENAMES": "Current-Alerts",
    "outputFormat": "application/json",
}

CLIENT_TIMEOUT = ClientTimeout(10)

CACHE_TTL = timedelta(minutes=5)

ATTRIBUTION = {
    "english": "Data provided by Environment Canada",
    "french": "Données fournies par Environnement Canada",
}

LOG = logging.getLogger(__name__)

__all__ = ["ECAlerts"]


@dataclass
class MetaData:
    attribution: str
    timestamp: datetime = datetime(1970, 1, 1, 0, 0, tzinfo=timezone.utc)


class ECAlerts:
    """Get weather alerts from Environment Canada GeoMet WFS."""

    def __init__(self, coordinates, language="english"):
        """Initialize ECAlerts with coordinates and language."""
        from .ec_weather import ALERTS_INIT  # lazy import to avoid circular dependency

        self.lat = coordinates[0]
        self.lon = coordinates[1]
        self.language = language
        self.metadata = MetaData(ATTRIBUTION[language])
        self.alerts = copy.deepcopy(ALERTS_INIT[language])
        self.alert_features = []

    async def update(self):
        """Fetch current alerts from Environment Canada GeoMet WFS."""
        from .ec_weather import ALERTS_INIT, ALERT_TYPE_TO_NAME  # lazy import

        cache_key = f"alerts-{self.language}-{self.lat:.4f}-{self.lon:.4f}"
        cached = Cache.get(cache_key)
        if cached is not None:
            self.alerts, self.alert_features = cached
            return

        bbox = (
            f"{self.lat - 2:.4f},{self.lon - 2:.4f},"
            f"{self.lat + 2:.4f},{self.lon + 2:.4f},EPSG:4326"
        )
        params = {**ALERTS_WFS_PARAMS, "BBOX": bbox}

        async with ClientSession(raise_for_status=True) as session:
            response = await session.get(
                GEOMET_WFS_URL,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=CLIENT_TIMEOUT,
            )
            data = await response.json()

        if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
            raise ValueError(f"Unexpected WFS response type: {type(data)}")

        lang_suffix = "en" if self.language == "english" else "fr"

        self.alerts = copy.deepcopy(ALERTS_INIT[self.language])
        self.alert_features = []

        for feature in data.get("features", []):
            props = feature.get("properties", {})
            if not isinstance(props, dict):
                continue

            self.alert_features.append(props)

            status_en = (props.get("status_en") or "").lower()
            alert_type = (props.get("alert_type") or "").lower()

            # Ended alerts go to endings category regardless of alert_type
            if status_en == "ended":
                category = "endings"
            else:
                category = ALERT_TYPE_TO_NAME.get(alert_type)
                if category is None:
                    continue

            title = props.get(f"alert_name_{lang_suffix}") or ""

            alert_dict = {
                "title": title.strip().title() if title else title,
                "date": props.get("publication_datetime"),
                "alertColourLevel": props.get(f"risk_colour_{lang_suffix}"),
                "expiryTime": props.get("expiration_datetime"),
                # Additive new fields
                "text": props.get(f"alert_text_{lang_suffix}"),
                "area": props.get(f"feature_name_{lang_suffix}"),
                "status": props.get(f"status_{lang_suffix}"),
                "confidence": props.get(f"confidence_{lang_suffix}"),
                "impact": props.get(f"impact_{lang_suffix}"),
                "alert_code": props.get("alert_code"),
            }

            self.alerts[category]["value"].append(alert_dict)

        Cache.add(cache_key, (self.alerts, self.alert_features), CACHE_TTL)
        self.metadata.timestamp = datetime.now(timezone.utc)

        LOG.debug(
            "update(): fetched %d alert features for (%f, %f)",
            len(self.alert_features),
            self.lat,
            self.lon,
        )
