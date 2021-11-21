import csv
import logging
import re
import xml.etree.ElementTree as et

from aiohttp import ClientSession
from dateutil import parser, tz
from geopy import distance
import voluptuous as vol

from . import ec_exc
from .constants import USER_AGENT

SITE_LIST_URL = "https://dd.weather.gc.ca/citypage_weather/docs/site_list_en.csv"

WEATHER_URL = "https://dd.weather.gc.ca/citypage_weather/xml/{}_{}.xml"

LOG = logging.getLogger(__name__)

ATTRIBUTION = {
    "english": "Data provided by Environment Canada",
    "french": "Données fournies par Environnement Canada",
}

conditions_meta = {
    "temperature": {
        "xpath": "./currentConditions/temperature",
        "type": "float",
        "english": "Temperature",
        "french": "Température",
    },
    "dewpoint": {
        "xpath": "./currentConditions/dewpoint",
        "type": "float",
        "english": "Dew Point",
        "french": "Point de rosée",
    },
    "wind_chill": {
        "xpath": "./currentConditions/windChill",
        "type": "int",
        "english": "Wind Chill",
        "french": "Refroidissement éolien",
    },
    "humidex": {
        "xpath": "./currentConditions/humidex",
        "type": "int",
        "english": "Humidex",
        "french": "Humidex",
    },
    "pressure": {
        "xpath": "./currentConditions/pressure",
        "type": "float",
        "english": "Pressure",
        "french": "Pression",
    },
    "tendency": {
        "xpath": "./currentConditions/pressure",
        "attribute": "tendency",
        "type": "str",
        "english": "Tendency",
        "french": "Tendance",
    },
    "humidity": {
        "xpath": "./currentConditions/relativeHumidity",
        "type": "int",
        "english": "Humidity",
        "french": "Humidité",
    },
    "visibility": {
        "xpath": "./currentConditions/visibility",
        "type": "float",
        "english": "Visibility",
        "french": "Visibilité",
    },
    "condition": {
        "xpath": "./currentConditions/condition",
        "type": "str",
        "english": "Condition",
        "french": "Condition",
    },
    "wind_speed": {
        "xpath": "./currentConditions/wind/speed",
        "type": "int",
        "english": "Wind Speed",
        "french": "Vitesse de vent",
    },
    "wind_gust": {
        "xpath": "./currentConditions/wind/gust",
        "type": "int",
        "english": "Wind Gust",
        "french": "Rafale de vent",
    },
    "wind_dir": {
        "xpath": "./currentConditions/wind/direction",
        "type": "str",
        "english": "Wind Direction",
        "french": "Direction de vent",
    },
    "wind_bearing": {
        "xpath": "./currentConditions/wind/bearing",
        "type": "int",
        "english": "Wind Bearing",
        "french": "Palier de vent",
    },
    "high_temp": {
        "xpath": './forecastGroup/forecast/temperatures/temperature[@class="high"]',
        "type": "int",
        "english": "High Temperature",
        "french": "Haute température",
    },
    "low_temp": {
        "xpath": './forecastGroup/forecast/temperatures/temperature[@class="low"]',
        "type": "int",
        "english": "Low Temperature",
        "french": "Basse température",
    },
    "uv_index": {
        "xpath": "./forecastGroup/forecast/uv/index",
        "type": "int",
        "english": "UV Index",
        "french": "Indice UV",
    },
    "pop": {
        "xpath": "./forecastGroup/forecast/abbreviatedForecast/pop",
        "type": "int",
        "english": "Chance of Precip.",
        "french": "Probabilité d'averses",
    },
    "icon_code": {
        "xpath": "./currentConditions/iconCode",
        "type": "str",
        "english": "Icon Code",
        "french": "Code icône",
    },
    "precip_yesterday": {
        "xpath": "./yesterdayConditions/precip",
        "type": "float",
        "english": "Precipitation Yesterday",
        "french": "Précipitation d'hier",
    },
    "normal_high": {
        "xpath": './forecastGroup/regionalNormals/temperature[@class="high"]',
        "type": "int",
        "english": "Normal High Temperature",
        "french": "Haute température normale",
    },
    "normal_low": {
        "xpath": './forecastGroup/regionalNormals/temperature[@class="low"]',
        "type": "int",
        "english": "Normal Low Temperature",
        "french": "Basse température normale",
    },
}

summary_meta = {
    "forecast_period": {
        "xpath": "./forecastGroup/forecast/period",
        "type": "str",
        "attribute": "textForecastName",
    },
    "text_summary": {"xpath": "./forecastGroup/forecast/textSummary", "type": "str"},
    "label": {"english": "Forecast", "french": "Prévision"},
}

alerts_meta = {
    "warnings": {
        "english": {"label": "Warnings", "pattern": ".*WARNING((?!ENDED).)*$"},
        "french": {
            "label": "Alertes",
            "pattern": ".*(ALERTE|AVERTISSEMENT)((?!TERMINÉ).)*$",
        },
    },
    "watches": {
        "english": {"label": "Watches", "pattern": ".*WATCH((?!ENDED).)*$"},
        "french": {"label": "Veilles", "pattern": ".*VEILLE((?!TERMINÉ).)*$"},
    },
    "advisories": {
        "english": {"label": "Advisories", "pattern": ".*ADVISORY((?!ENDED).)*$"},
        "french": {"label": "Avis", "pattern": ".*AVIS((?!TERMINÉ).)*$"},
    },
    "statements": {
        "english": {"label": "Statements", "pattern": ".*STATEMENT((?!ENDED).)*$"},
        "french": {"label": "Bulletins", "pattern": ".*BULLETIN((?!TERMINÉ).)*$"},
    },
    "endings": {
        "english": {"label": "Endings", "pattern": ".*ENDED"},
        "french": {"label": "Terminaisons", "pattern": ".*TERMINÉE?"},
    },
}

metadata_meta = {
    "timestamp": {"xpath": "./currentConditions/dateTime/timeStamp"},
    "location": {"xpath": "./location/name"},
    "station": {"xpath": "./currentConditions/station"},
}


def validate_station(station):
    """Check that the station ID is well-formed."""
    if station is None:
        return
    if not re.fullmatch(r"[A-Z]{2}/s0000\d{3}", station):
        raise vol.error.Invalid('Station ID must be of the form "XX/s0000###"')
    return station


def parse_timestamp(t):
    return parser.parse(t).replace(tzinfo=tz.UTC)


async def get_ec_sites():
    """Get list of all sites from Environment Canada, for auto-config."""
    sites = []

    async with ClientSession(raise_for_status=True) as session:
        response = await session.get(
            SITE_LIST_URL, headers={"User-Agent": USER_AGENT}, timeout=10
        )
        sites_csv_string = await response.text()

    sites_reader = csv.DictReader(sites_csv_string.splitlines()[1:])

    for site in sites_reader:
        if site["Province Codes"] != "HEF":
            site["Latitude"] = float(site["Latitude"].replace("N", ""))
            site["Longitude"] = -1 * float(site["Longitude"].replace("W", ""))
            sites.append(site)

    return sites


def closest_site(site_list, lat, lon):
    """Return the province/site_code of the closest station to our lat/lon."""

    def site_distance(site):
        """Calculate distance to a site."""
        return distance.distance((lat, lon), (site["Latitude"], site["Longitude"]))

    closest = min(site_list, key=site_distance)

    return "{}/{}".format(closest["Province Codes"], closest["Codes"])


class ECWeather(object):

    """Get weather data from Environment Canada."""

    def __init__(self, **kwargs):
        """Initialize the data object."""

        init_schema = vol.Schema(
            vol.All(
                {
                    vol.Required(
                        vol.Any("station_id", "coordinates"),
                        msg="Must specify either 'station_id' or 'coordinates'",
                    ): object,
                    vol.Optional("language"): object,
                },
                {
                    vol.Optional("station_id"): validate_station,
                    vol.Optional("coordinates"): (
                        vol.All(vol.Or(int, float), vol.Range(-90, 90)),
                        vol.All(vol.Or(int, float), vol.Range(-180, 180)),
                    ),
                    vol.Optional("language", default="english"): vol.In(
                        ["english", "french"]
                    ),
                },
            )
        )

        kwargs = init_schema(kwargs)

        self.language = kwargs["language"]
        self.metadata = {"attribution": ATTRIBUTION[self.language]}
        self.conditions = {}
        self.alerts = {}
        self.daily_forecasts = []
        self.hourly_forecasts = []
        self.forecast_time = ""

        if "station_id" in kwargs and kwargs["station_id"] is not None:
            self.station_id = kwargs["station_id"]
            self.lat = None
            self.lon = None
        else:
            self.station_id = None
            self.lat = kwargs["coordinates"][0]
            self.lon = kwargs["coordinates"][1]

    async def update(self):
        """Get the latest data from Environment Canada."""

        # Determine station ID or coordinates if not provided
        site_list = await get_ec_sites()
        if self.station_id:
            stn = self.station_id.split("/")
            if len(stn) == 2:
                for site in site_list:
                    if stn[1] == site["Codes"] and stn[0] == site["Province Codes"]:
                        self.lat = site["Latitude"]
                        self.lon = site["Longitude"]
                        break
            if not self.lat:
                raise ec_exc.UnknownStationId
        else:
            self.station_id = closest_site(site_list, self.lat, self.lon)
            if not self.station_id:
                raise ec_exc.UnknownStationId

        # Get weather data

        async with ClientSession(raise_for_status=True) as session:
            response = await session.get(
                WEATHER_URL.format(self.station_id, self.language[0]),
                headers={"User-Agent": USER_AGENT},
                timeout=10,
            )
            result = await response.read()
        weather_xml = result.decode("iso-8859-1")

        try:
            weather_tree = et.fromstring(weather_xml)
        except et.ParseError:
            raise ECWeatherUpdateFailed("Weather update failed")

        # Update metadata
        for m, meta in metadata_meta.items():
            element = weather_tree.find(meta["xpath"])
            if element is not None:
                self.metadata[m] = weather_tree.find(meta["xpath"]).text
                if m == "timestamp":
                    self.metadata[m] = parse_timestamp(self.metadata[m])
            else:
                self.metadata[m] = None

        # Update current conditions
        def get_condition(meta):
            condition = {}

            element = weather_tree.find(meta["xpath"])

            if element is None or element.text is None:
                condition["value"] = None
            else:
                if meta.get("attribute"):
                    condition["value"] = element.attrib.get(meta["attribute"])
                else:
                    if meta["type"] == "int":
                        condition["value"] = int(float(element.text))

                    elif meta["type"] == "float":
                        if element.text == "Trace":
                            condition["value"] = float(0)
                        else:
                            condition["value"] = float(element.text)

                    else:
                        condition["value"] = element.text

                    if element.attrib.get("units"):
                        condition["unit"] = element.attrib.get("units")
            return condition

        for c, meta in conditions_meta.items():
            self.conditions[c] = {"label": meta[self.language]}
            self.conditions[c].update(get_condition(meta))

        # Update text summary
        period = get_condition(summary_meta["forecast_period"])["value"]
        summary = get_condition(summary_meta["text_summary"])["value"]

        self.conditions["text_summary"] = {
            "label": summary_meta["label"][self.language],
            "value": ". ".join([period, summary]),
        }

        # Update alerts
        for category, meta in alerts_meta.items():
            self.alerts[category] = {"value": [], "label": meta[self.language]["label"]}

        alert_elements = weather_tree.findall("./warnings/event")

        for a in alert_elements:
            title = a.attrib.get("description").strip()
            for category, meta in alerts_meta.items():
                category_match = re.search(meta[self.language]["pattern"], title)
                if category_match:
                    alert = {
                        "title": title.title(),
                        "date": a.find("./dateTime[last()]/textSummary").text,
                    }
                    self.alerts[category]["value"].append(alert)

        # Update forecasts
        self.forecast_time = parse_timestamp(
            weather_tree.findtext("./forecastGroup/dateTime/timeStamp")
        )
        self.daily_forecasts = []
        self.hourly_forecasts = []

        # Update daily forecasts
        for f in weather_tree.findall("./forecastGroup/forecast"):
            self.daily_forecasts.append(
                {
                    "period": f.findtext("period"),
                    "text_summary": f.findtext("textSummary"),
                    "icon_code": f.findtext("./abbreviatedForecast/iconCode"),
                    "temperature": int(f.findtext("./temperatures/temperature")),
                    "temperature_class": f.find(
                        "./temperatures/temperature"
                    ).attrib.get("class"),
                    "precip_probability": int(
                        f.findtext("./abbreviatedForecast/pop") or "0"
                    ),
                }
            )

        # Update hourly forecasts
        for f in weather_tree.findall("./hourlyForecastGroup/hourlyForecast"):
            self.hourly_forecasts.append(
                {
                    "period": parse_timestamp(f.attrib.get("dateTimeUTC")),
                    "condition": f.findtext("./condition"),
                    "temperature": int(f.findtext("./temperature")),
                    "icon_code": f.findtext("./iconCode"),
                    "precip_probability": int(f.findtext("./lop") or "0"),
                }
            )


class ECWeatherUpdateFailed(Exception):
    """Raised when an update fails to get usable data."""
