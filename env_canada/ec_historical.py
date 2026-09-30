import asyncio
import csv
import logging
import re
from datetime import date, datetime, time
from io import StringIO

import lxml.html
import pandas as pd
import voluptuous as vol
from aiohttp import ClientSession
from dateutil import parser, tz
from dateutil.relativedelta import relativedelta
from lxml import etree as et

from . import ec_exc
from .constants import CLIENT_TIMEOUT, USER_AGENT

STATIONS_URL = "https://climate.weather.gc.ca/historical_data/search_historic_data_stations_{}.html"

WEATHER_URL = "https://climate.weather.gc.ca/climate_data/bulk_data_{}.html"

LOG = logging.getLogger(__name__)

__all__ = ["ECHistorical"]

stationdata_meta = {
    "maxtemp": {
        "xpath": "./maxtemp",
        "type": "float",
        "units": "°C",
        "english": "Maximum Temperature",
        "french": "Température maximale",
    },
    "mintemp": {
        "xpath": "./mintemp",
        "type": "float",
        "units": "°C",
        "english": "Minimum Temperature",
        "french": "Température minimale",
    },
    "meantemp": {
        "xpath": "./meantemp",
        "type": "float",
        "units": "°C",
        "english": "Mean Temperature",
        "french": "Température moyenne",
    },
    "heatdegdays": {
        "xpath": "./heatdegdays",
        "type": "float",
        "units": "°C",
        "english": "Heating Degree Days",
        "french": "Degré-jour de chauffage",
    },
    "cooldegdays": {
        "xpath": "./cooldegdays",
        "type": "float",
        "units": "°C",
        "english": "Cooling Degree Days",
        "french": "Degré-jour de réfrigération",
    },
    "totalrain": {
        "xpath": "./totalrain",
        "type": "float",
        "units": "mm",
        "english": "Total Rain",
        "french": "Pluie totale",
    },
    "totalsnow": {
        "xpath": "./totalsnow",
        "type": "float",
        "units": "cm",
        "english": "Total Snow",
        "french": "Neige totale",
    },
    "totalprecipitation": {
        "xpath": "./totalprecipitation",
        "type": "float",
        "units": "mm",
        "english": "Total Precipitation",
        "french": "Précipitations totales",
    },
    "snowonground": {
        "xpath": "./snowonground",
        "type": "float",
        "units": "cm",
        "english": "Snow on Ground",
        "french": "Neige au sol",
    },
    "dirofmaxgust": {
        "xpath": "./dirofmaxgust",
        "type": "int",
        "units": "10s Deg",
        "english": "Direction of Maximum Gust",
        "french": "Direction de la rafale maximale",
    },
    "speedofmaxgust": {
        "xpath": "./speedofmaxgust",
        "type": "int",
        "units": "km/h",
        "english": "Speed of Maximum Gust",
        "french": "Vitesse de la rafale maximale",
    },
}

# The elements of an hourly <stationdata> record and how to read each. Unlike
# the daily ones they are labelled, in the language asked for, by the service.
hourlydata_meta = {
    "temp": "float",
    "dptemp": "float",
    "relhum": "int",
    "precipamt": "float",
    "winddir": "int",
    "windspd": "int",
    "visibility": "float",
    "stnpress": "float",
    "humidex": "int",
    "windchill": "int",
    "weather": "str",
}

metadata_meta = {
    "name": {"xpath": "./stationinformation/name"},
    "province": {"xpath": "./stationinformation/province_or_territory"},
    "stationoperator": {"xpath": "./stationinformation/stationoperator"},
    "latitude": {"xpath": "./stationinformation/latitude"},
    "longitude": {"xpath": "./stationinformation/longitude"},
    "elevation": {"xpath": "./stationinformation/elevation"},
    "climate_identifier": {"xpath": "./stationinformation/climate_identifier"},
    "wmo_identifier": {"xpath": "./stationinformation/wmo_identifier"},
    "tc_identifier": {"xpath": "./stationinformation/tc_identifier"},
}


def parse_timestamp(t):
    return parser.parse(t).replace(tzinfo=tz.UTC)


def _parse_value(text, kind):
    """Read a value from a <stationdata> element, or None for a blank one. The
    service writes a missing value as empty text, and sometimes as a single
    space; in French it writes the decimal point as a comma."""
    text = (text or "").strip()
    if not text:
        return None
    if kind == "str":
        return text
    text = text.replace(",", ".")
    return int(float(text)) if kind == "int" else float(text)


def _read_element(element, kind, label):
    """One value of a record: the number or text, its unit when it has one,
    and its label."""
    value = None if element is None else _parse_value(element.text, kind)
    record = {"value": value}
    if value is not None and element.attrib.get("units"):
        record["unit"] = element.attrib["units"]
    record["label"] = label
    return record


def _default_daterange():
    """The last year or so, up to today. Worked out when it is wanted: a
    window fixed when the module was imported goes stale in a process that
    stays up."""
    today = date.today()
    return today - relativedelta(years=1, months=1, day=1), today


def _as_datetimes(daterange):
    """The range as datetimes, earliest first. A date is midnight as a start
    and the end of the day as a stop, so that a range of dates covers every
    hour of both."""
    start, stop = daterange
    if not isinstance(start, datetime):
        start = datetime.combine(start, time.min)
    if not isinstance(stop, datetime):
        stop = datetime.combine(stop, time.max)
    return (stop, start) if start > stop else (start, stop)


def _xml_name(name):
    """A column name as an XML tag name. pandas writes the names as tags, and
    the service's - "Date/Time", "Max Temp (°C)" - aren't valid ones."""
    name = re.sub(r"\W+", "_", str(name)).strip("_") or "_"
    return "_" + name if name[0].isdigit() else name


def coerce_station_id(v):
    """Coerce station_id to string, accepting both int and str inputs."""
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return v
    raise vol.Invalid("station_id must be a string or integer")


async def get_historical_stations(
    coordinates,
    radius=25,
    start_year=1840,
    end_year=None,
    limit=25,
    language="english",
    timeframe=2,
    month=1,
):
    """Get list of all historical stations from Environment Canada"""
    lat, lng = coordinates
    if end_year is None:
        end_year = datetime.today().year
    params = {
        "searchType": "stnProx",
        "timeframe": timeframe,
        "txtRadius": radius,
        "optProxType": "decimal",
        "txtLatDecDeg": lat,
        "txtLongDecDeg": lng,
        "optLimit": "yearRange",
        "StartYear": start_year,
        "EndYear": end_year,
        "Year": start_year,
        "Month": "1",
        "Day": "1",
        "selRowPerPage": limit,
        "selCity": "",
        "selPark": "",
        "txtCentralLatDeg": "",
        "txtCentralLatMin": "",
        "txtCentralLatSec": "",
        "txtCentralLongDeg": "",
        "txtCentralLongMin": "",
        "txtCentralLongSec": "",
    }

    async with ClientSession(raise_for_status=True) as session:
        response = await session.get(
            STATIONS_URL.format(language[0]),
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=CLIENT_TIMEOUT,
        )
        result = await response.read()

        station_html = result.decode("utf-8")
        station_tree = lxml.html.fromstring(station_html)
        station_req_forms = station_tree.xpath(
            "//form[starts-with(@id, 'stnRequest') and '-sm' = substring(@id, string-length(@id) - string-length('-sm') +1)]"
        )

        stations = {}
        for station_req_form in station_req_forms:
            station = {}
            station_table = station_req_form.xpath(
                './/div[@class="col-md-10 col-sm-8 col-xs-8"]'
            )
            station_name = station_table[0].text
            station["prov"] = station_table[1].text
            station["proximity"] = float(station_table[2].text)
            station["id"] = station_req_form.find(
                "input[@name='climate_id']"
            ).attrib.get("value")
            station["hlyRange"] = station_req_form.find(
                "input[@name='hlyRange']"
            ).attrib.get("value")
            station["dlyRange"] = station_req_form.find(
                "input[@name='dlyRange']"
            ).attrib.get("value")
            station["mlyRange"] = station_req_form.find(
                "input[@name='mlyRange']"
            ).attrib.get("value")
            stations[station_name] = station

        return stations


class ECHistorical:
    """Get historical weather data from Environment Canada."""

    def __init__(self, **kwargs):
        """Initialize the data object."""

        init_schema = vol.Schema(
            {
                vol.Required("station_id"): vol.All(coerce_station_id),
                vol.Required("year"): vol.All(
                    int, vol.Range(1840, datetime.today().year)
                ),
                vol.Required("month", default=1): vol.All(int, vol.Range(1, 12)),
                vol.Required("day", default=1): vol.All(int, vol.Range(1, 31)),
                vol.Required("language", default="english"): vol.In(
                    ["english", "french"]
                ),
                vol.Required("format", default="xml"): vol.In(["xml", "csv"]),
                vol.Required("timeframe", default=2): vol.In([1, 2]),
            }
        )

        kwargs = init_schema(kwargs)

        self.station_id = kwargs["station_id"]
        self.timeframe = kwargs["timeframe"]
        self.year = kwargs["year"]
        self.month = kwargs["month"]
        self.day = kwargs["day"]
        self.language = kwargs["language"]
        self.format = kwargs["format"]
        self.submit = "Download+Data"

        self.metadata = {}
        self.station_data = {}

    async def update(self):
        """Get the historical data from Environment Canada. Raises
        `ec_exc.UnknownStationId` if the service has no such station."""

        params = {
            "climate_id": self.station_id,
            "Year": self.year,
            "Month": self.month,
            "Day": self.day,
            "format": self.format,
            "timeframe": self.timeframe,
            "submit": self.submit,
        }

        # Get historical weather data

        async with ClientSession(raise_for_status=True) as session:
            response = await session.get(
                WEATHER_URL.format(self.language[0]),
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=CLIENT_TIMEOUT,
            )
            if self.format == "csv":
                self._update_from_csv(await response.text())
            else:
                self._update_from_xml(await response.read())

    def _unknown_station(self):
        return ec_exc.UnknownStationId(
            f"No historical data for station {self.station_id}"
        )

    def _update_from_csv(self, result):
        reader = csv.reader(StringIO(result))

        # headers
        next(reader, None)

        # first row of data. A station the service doesn't have gets a file
        # of just the headers.
        firstrow = next(reader, None)
        if firstrow is None:
            raise self._unknown_station()

        self.station_data = StringIO(result)
        self.metadata = {
            "longitude": firstrow[0],
            "latitude": firstrow[1],
            "name": firstrow[2],
            "climate_identifier": firstrow[3],
        }

    def _update_from_xml(self, result):
        # A station the service doesn't have gets a document of just a
        # byte-order mark.
        if not result.decode("utf-8-sig").strip():
            raise self._unknown_station()

        # The bytes, not text: lxml refuses text that opens with an encoding
        # declaration, and the byte-order mark the service writes before its
        # declaration was all that kept it from noticing.
        weather_tree = et.fromstring(result)

        metadata = {}
        for m, meta in metadata_meta.items():
            element = weather_tree.find(meta["xpath"])
            metadata[m] = None if element is None else element.text

        station_data = {}
        for stationdata_element in weather_tree.findall("./stationdata"):
            attrib = stationdata_element.attrib
            if self.timeframe == 1:
                stamp = datetime(
                    int(attrib["year"]),
                    int(attrib["month"]),
                    int(attrib["day"]),
                    int(attrib["hour"]),
                    int(attrib.get("minute", 0)),
                )
                key = stamp.strftime("%Y-%m-%d %H:%M")
                station_data[key] = {
                    tag: _read_element(
                        stationdata_element.find(f"./{tag}"),
                        kind,
                        self._hourly_label(stationdata_element, tag),
                    )
                    for tag, kind in hourlydata_meta.items()
                }
            else:
                dt = parse_timestamp(
                    f"{attrib.get('year')}-{attrib.get('month')}-{attrib.get('day')}"
                ).date()
                station_data[str(dt)] = {
                    s: _read_element(
                        stationdata_element.find(meta["xpath"]),
                        meta["type"],
                        meta[self.language],
                    )
                    for s, meta in stationdata_meta.items()
                }

        self.metadata = metadata
        self.station_data = station_data

    @staticmethod
    def _hourly_label(stationdata_element, tag):
        element = stationdata_element.find(f"./{tag}")
        return tag if element is None else element.attrib.get("description", tag)


class ECHistoricalRange:
    """Get historical weather data from Environment Canada in the given range for the given station.

        options are daily or hourly data

        Example:
            import pandas as pd
            import asyncio
            from env_canada import ECHistoricalRange, get_historical_stations
            from datetime import datetime

            coordinates = ['48.508333', '-68.467667']

            stations = pd.DataFrame(asyncio.run(get_historical_stations(coordinates, start_year=2022,
                                                            end_year=2022, radius=200, limit=100))).T

            ec = ECHistoricalRange(station_id=stations.iloc[0,2], timeframe="hourly",
                                    daterange=(datetime(2022, 7, 1, 12, 12), datetime(2022, 8, 1, 12, 12)))

            ec.get_data()  # or, from async code, `await ec.update()`

            ec.xml #yield an XML formatted str. For more options, use ec.to_xml(*arg, **kwargs) with pandas options
    =
            ec.csv #yield an CSV formatted str. For more options, use ec.to_csv(*arg, **kwargs) with pandas options
    """

    def __init__(
        self,
        station_id,
        daterange=None,
        language="english",
        timeframe="daily",
    ):
        """
        Return a DataFrame containing the data from the date range
        :param station_id: the ID of the station found with get_historical_stations
        :type station_id: str or int
        :param daterange: the dates between which the data are retrieve, in either
            order; the last year or so up to today if omitted. A date, rather than a
            datetime, as the end covers that whole day
        :type daterange: tuple of datetime or date
        :param language: language in which the data are retrieved
        :type language: str
        :param timeframe: selection of granularity : 'hourly' or 'daily'
        :type timeframe: str
        """
        if language not in ("english", "french"):
            raise vol.Invalid("language must be 'english' or 'french'")
        _tf = {"hourly": 1, "daily": 2}
        if timeframe not in _tf:
            raise vol.Invalid("timeframe must be 'hourly' or 'daily'")

        self.df = pd.DataFrame()

        self.station_id = str(station_id)
        self.startdate, self.stopdate = _as_datetimes(daterange or _default_daterange())
        self.months = self.monthlist(daterange=(self.startdate, self.stopdate))
        self.language = language
        self.timeframe = _tf[timeframe]
        if self.timeframe == 2:
            # A daily request is answered with the whole year, so ask once for
            # each year in the range
            self.months = [(year, 1) for year in sorted({y for y, _ in self.months})]

    async def update(self):
        """
        Fetch the data, one request after another so as not to hammer the service
        :return: All data in the range
        :rtype: pd.DataFrame
        """
        decimal = "," if self.language == "french" else "."
        frames = []
        for year, month in self.months:
            data = ECHistorical(
                station_id=self.station_id,
                year=year,
                month=month,
                language=self.language,
                format="csv",
                timeframe=self.timeframe,
            )
            await data.update()
            frames.append(pd.read_csv(data.station_data, decimal=decimal))

        df = pd.concat(frames)
        df = df.set_index(df.filter(regex="Date/*", axis=1).columns.to_numpy()[0])
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()

        # removing the dates before and after the range as the data received might exceed the range
        self.df = df[(self.startdate <= df.index) & (df.index <= self.stopdate)]

        return self.df

    def get_data(self):
        """
        Get data from creating instance of ECHistorical. Can't be called from a
        running event loop: use `await update()` there.
        :return: All data in the range
        :rtype: pd.DataFrame
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.update())
        raise RuntimeError(
            "get_data() can't run inside a running event loop; await update() instead"
        )

    @property
    def xml(self):
        return self.to_xml()

    def to_xml(self, *args, **kwargs):
        df = self.df if not self.df.empty else self.get_data()
        df = df.rename(columns=_xml_name).rename_axis(index=_xml_name(df.index.name))
        return df.to_xml(*args, **kwargs)

    @property
    def csv(self):
        if self.language == "french":
            decimal = ","
        else:
            decimal = "."
        sep = ";"
        encoding = "utf-8-sig"
        return self.to_csv(sep=sep, decimal=decimal, encoding=encoding)

    def to_csv(self, *args, **kwargs):
        if not self.df.empty:
            return self.df.to_csv(*args, **kwargs)
        else:
            return self.get_data().to_csv(*args, **kwargs)

    def monthlist(self, daterange):
        startdate, stopdate = _as_datetimes(daterange)

        def total_months(dt):
            return dt.month + 12 * dt.year

        mlist = []
        for tot_m in range(total_months(startdate) - 1, total_months(stopdate)):
            y, m = divmod(tot_m, 12)
            mlist.append((y, m + 1))
        return mlist
