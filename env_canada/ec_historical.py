import asyncio
import copy
import csv
import logging
from datetime import datetime
from io import StringIO

import lxml.html
import pandas as pd
import voluptuous as vol
from aiohttp import ClientSession
from dateutil import parser, tz
from dateutil.relativedelta import relativedelta
from lxml import etree as et

from .constants import USER_AGENT

STATIONS_URL = "https://climate.weather.gc.ca/historical_data/search_historic_data_stations_{}.html"

WEATHER_URL = "https://climate.weather.gc.ca/climate_data/bulk_data_{}.html"

_TODAY = datetime.today().date()
_ONE_YEAR_AGO = _TODAY - relativedelta(years=1, months=1, day=1)
_YEAR = datetime.today().year

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

metadata_meta = {
    "name": {"xpath": "./stationinformation/name"},
    "province": {"xpath": "./stationinformation/province"},
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


async def get_historical_stations(
    coordinates,
    radius=25,
    start_year=1840,
    end_year=_YEAR,
    limit=25,
    language="english",
    timeframe=2,
    month=1,
):
    """Get list of all historical stations from Environment Canada"""
    lat, lng = coordinates
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
            timeout=10,
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
            station_name = station_req_form.xpath(
                './/div[@class="col-md-10 col-sm-8 col-xs-8"]'
            )[0].text
            station["prov"] = station_req_form.xpath(
                './/div[@class="col-md-10 col-sm-8 col-xs-8"]'
            )[1].text
            station["proximity"] = float(
                station_req_form.xpath('.//div[@class="col-md-10 col-sm-8 col-xs-8"]')[
                    2
                ].text
            )
            station["id"] = station_req_form.find(
                "input[@name='StationID']"
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
                vol.Required("station_id"): int,
                vol.Required("year"): vol.All(
                    int, vol.Range(1840, datetime.today().year)
                ),
                vol.Required("month", default=1): vol.All(int, vol.Range(1, 12)),
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
        self.language = kwargs["language"]
        self.format = kwargs["format"]
        self.submit = "Download+Data"

        self.metadata = {}
        self.station_data = {}

    async def update(self):
        """Get the historical data from Environment Canada."""

        params = {
            "stationID": self.station_id,
            "Year": self.year,
            "Month": self.month,
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
                timeout=10,
            )
            if self.format == "csv":
                result = await response.text()

                f = StringIO(result)

                self.station_data = copy.deepcopy(f)

                reader = csv.reader(f, delimiter=",")

                # headers
                next(reader)

                # first row of data
                firstrow = next(reader)

                self.metadata = {
                    "longitude": firstrow[0],
                    "latitude": firstrow[1],
                    "name": firstrow[2],
                    "climate_identifier": firstrow[3],
                }

            else:
                result = await response.read()

                weather_xml = result.decode("utf-8")
                weather_tree = et.fromstring(weather_xml)

                # Update metadata
                for m, meta in metadata_meta.items():
                    element = weather_tree.find(meta["xpath"])
                    if element is not None:
                        self.metadata[m] = weather_tree.find(meta["xpath"]).text
                    else:
                        self.metadata[m] = None

                # Update station data
                def get_stationdata(meta, stationdata_element, language):
                    stationdata = {}

                    element = stationdata_element.find(meta["xpath"])

                    if element is None or element.text is None:
                        stationdata["value"] = None
                    else:
                        if meta.get("attribute"):
                            stationdata["value"] = element.attrib.get(meta["attribute"])
                        else:
                            if meta["type"] == "int":
                                stationdata["value"] = int(element.text)
                            elif meta["type"] == "float":
                                stationdata["value"] = float(
                                    element.text.replace(",", ".")
                                )
                            else:
                                stationdata["value"] = element.text

                            if element.attrib.get("units"):
                                stationdata["unit"] = element.attrib.get("units")
                    stationdata["label"] = meta[language]
                    return stationdata

                stationdata_elements = weather_tree.findall("./stationdata")

                for stationdata_element in stationdata_elements:
                    day = stationdata_element.attrib.get("day")
                    month = stationdata_element.attrib.get("month")
                    year = stationdata_element.attrib.get("year")
                    dt = parse_timestamp(f"{year}-{month}-{day}").date()

                    cur_station_data = {}

                    for s, meta in stationdata_meta.items():
                        cur_station_data[s] = get_stationdata(
                            meta, stationdata_element, self.language
                        )

                    self.station_data[str(dt)] = cur_station_data


def flip_daterange(f):
    def wrapper(*args, **kwargs):
        if kwargs.get("daterange") in globals():
            if kwargs.get("daterange")[0] > kwargs.get("daterange")[1]:
                kwargs["daterange"] = (
                    kwargs.get("daterange")[1],
                    kwargs.get("daterange")[0],
                )
        return f(*args, **kwargs)

    return wrapper


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

            ec = ECHistoricalRange(station_id=int(stations.iloc[0,2]), timeframe="hourly",
                                    daterange=(datetime(2022, 7, 1, 12, 12), datetime(2022, 8, 1, 12, 12)))

            ec.get_data()

            ec.xml #yield an XML formatted str. For more options, use ec.to_xml(*arg, **kwargs) with pandas options
    =
            ec.csv #yield an CSV formatted str. For more options, use ec.to_csv(*arg, **kwargs) with pandas options
    """

    @flip_daterange
    def __init__(
        self,
        station_id,
        daterange=(
            _ONE_YEAR_AGO,
            _TODAY,
        ),
        language="english",
        timeframe="daily",
    ):
        """
        Return a DataFrame containing the data from the date range
        :param station_id: the ID of the station found with get_historical_stations
        :type station_id: int
        :param daterange: the dates between which the data are retrieve
        :type daterange: tuple of datetime
        :param language: language in which the data are retrieved
        :type language: str
        :param timeframe: selection of granularity : 'hourly', 'daily' [or 'monthly'](to be implemented)
        :type timeframe: str
        """

        self.df = pd.DataFrame()

        self.station_id = int(station_id)
        self.startdate, self.stopdate = daterange
        self.months = self.monthlist(daterange=daterange)
        self.language = language
        _tf = {"hourly": 1, "daily": 2, "monthly": 3}
        timeframe_int = _tf[timeframe]
        if timeframe_int == 2:
            # prune the months list so it only has unique years. if daily is selected.
            years = set()
            for year, _ in self.months:
                years.add(year)
            self.months = [(year, 1) for year in years]
        self.timeframe = timeframe_int

    def get_data(self):
        """
        Get data from creating instance of ECHistorical
        :return: All data in the range
        :rtype: pd.DataFrame
        """
        if not self.df.empty:
            self.df = pd.DataFrame()
        ec = [
            ECHistorical(
                station_id=self.station_id,
                year=year,
                month=month,
                language=self.language,
                format="csv",
                timeframe=self.timeframe,
            )
            for year, month in self.months
        ]

        for data in ec:
            asyncio.run(data.update())
            self.df = pd.concat((self.df, pd.read_csv(data.station_data)))

        self.df = self.df.set_index(
            self.df.filter(regex="Date/*", axis=1).columns.to_numpy()[0]
        )
        self.df.index = pd.to_datetime(self.df.index)

        # removing the dates before and after the range as the data received might exceed the range
        self.df = self.df[self.startdate <= self.df.index]
        self.df = self.df[self.stopdate >= self.df.index]

        return self.df

    @property
    def xml(self):
        encoding = "utf-8-sig"
        return self.to_xml(encoding=encoding)

    def to_xml(self, *args, **kwargs):
        if not self.df.empty:
            return self.df.to_xml(*args, **kwargs)
        else:
            return self.get_data().to_xml(*args, **kwargs)

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

    @flip_daterange
    def monthlist(self, daterange):
        startdate, stopdate = daterange

        def total_months(dt):
            return dt.month + 12 * dt.year

        mlist = []
        for tot_m in range(total_months(startdate) - 1, total_months(stopdate)):
            y, m = divmod(tot_m, 12)
            mlist.append((y, m + 1))
        return mlist
