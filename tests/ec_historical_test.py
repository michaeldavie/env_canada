import asyncio
import threading
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd
import pytest
import voluptuous as vol
from aiohttp import web
from freezegun import freeze_time

from env_canada import ECHistorical, ECHistoricalRange, ec_exc, ec_historical
from env_canada.ec_historical import get_historical_stations

# Responses captured from climate.weather.gc.ca for Prince George Airport
# (climate ID 1096453) and trimmed: XML and CSV files keep the header and the
# legend, and a handful of whole records or rows; the station search keeps
# its four result forms. `unknown_station.*` are whole responses, for a
# climate ID the service doesn't have.
FIXTURES = Path(__file__).parent / "fixtures" / "historical"
XML = "text/xml;charset=UTF-8"
CSV = "application/force-download"
HTML = "text/html; charset=utf-8"
UNKNOWN_STATION = "9999999"

# (language, format, timeframe, year, month) -> (fixture, content type), with
# the content types as the service sends them. A daily request is answered
# with the whole year whatever the month, so those entries have no month.
RESPONSES = {
    ("e", "xml", 2, 2021, None): ("daily_en.xml", XML),
    ("f", "xml", 2, 2021, None): ("daily_fr.xml", XML),
    ("e", "xml", 1, 2021, 5): ("hourly_en.xml", XML),
    ("f", "xml", 1, 2021, 5): ("hourly_fr.xml", XML),
    ("e", "csv", 2, 2021, None): ("daily_2021_en.csv", CSV),
    ("f", "csv", 2, 2021, None): ("daily_2021_fr.csv", CSV),
    ("e", "csv", 2, 2020, None): ("daily_2020_en.csv", CSV),
    ("e", "csv", 1, 2021, 4): ("hourly_2021_04_en.csv", CSV),
    ("e", "csv", 1, 2021, 5): ("hourly_2021_05_en.csv", CSV),
}
UNKNOWN = {
    "csv": ("unknown_station.csv", CSV),
    "xml": ("unknown_station.xml", XML),
}


class ClimateService:
    """A local stand-in for climate.weather.gc.ca that answers with the
    captured responses, and keeps the query of every request it gets."""

    def __init__(self):
        self.requests = []
        self.port = None
        # (body, content type) to answer every bulk-data request with instead
        self.override = None

    async def bulk_data(self, request):
        query = request.query
        self.requests.append(dict(query))
        if self.override:
            body, content_type = self.override
            return web.Response(body=body, headers={"Content-Type": content_type})
        if query["climate_id"] == UNKNOWN_STATION:
            name, content_type = UNKNOWN[query["format"]]
        else:
            timeframe = int(query["timeframe"])
            key = (
                request.match_info["language"],
                query["format"],
                timeframe,
                int(query["Year"]),
                int(query["Month"]) if timeframe == 1 else None,
            )
            if key not in RESPONSES:
                raise web.HTTPNotFound(text=f"no captured response for {key}")
            name, content_type = RESPONSES[key]
        return web.Response(
            body=(FIXTURES / name).read_bytes(), headers={"Content-Type": content_type}
        )

    async def stations(self, request):
        self.requests.append(dict(request.query))
        return web.Response(
            body=(FIXTURES / "stations_en.html").read_bytes(),
            headers={"Content-Type": HTML},
        )


@pytest.fixture
def climate(monkeypatch):
    """Point the library at a ClimateService. It runs on its own thread and
    event loop, because ECHistoricalRange.get_data() runs a loop of its own
    and so can't be called from a test that is hosting the server on one."""
    service = ClimateService()
    app = web.Application()
    app.router.add_get("/climate_data/bulk_data_{language}.html", service.bulk_data)
    app.router.add_get(
        "/historical_data/search_historic_data_stations_{language}.html",
        service.stations,
    )
    loop = asyncio.new_event_loop()
    runner = web.AppRunner(app)
    started = threading.Event()

    def serve():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", 0)
        loop.run_until_complete(site.start())
        service.port = site._server.sockets[0].getsockname()[1]
        started.set()
        loop.run_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert started.wait(10)

    base = f"http://127.0.0.1:{service.port}"
    monkeypatch.setattr(
        ec_historical, "WEATHER_URL", base + "/climate_data/bulk_data_{}.html"
    )
    monkeypatch.setattr(
        ec_historical,
        "STATIONS_URL",
        base + "/historical_data/search_historic_data_stations_{}.html",
    )
    yield service

    asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(10)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(10)
    loop.close()


def historical(**kwargs):
    kwargs = {"station_id": 1096453, "year": 2021, **kwargs}
    return ECHistorical(**kwargs)


def updated(**kwargs):
    weather = historical(**kwargs)
    asyncio.run(weather.update())
    return weather


@pytest.mark.parametrize(
    "init_parameters",
    [
        {"station_id": 1096453, "year": 2021},
        {"station_id": "1096453", "year": 2021},
        {"station_id": "10476F0", "year": 2021},
        {"station_id": 1096453, "year": 2021, "language": "english"},
        {"station_id": 1096453, "year": 2021, "language": "french"},
        {"station_id": 1096453, "year": 2021, "format": "csv"},
        {"station_id": 1096453, "year": 2021, "format": "xml"},
        {
            "station_id": 1096453,
            "year": 2021,
            "month": 5,
            "format": "xml",
            "timeframe": 1,
        },
        {"station_id": 1096453, "year": 2021, "format": "csv", "timeframe": 1},
    ],
)
def test_echistorical(init_parameters):
    weather = ECHistorical(**init_parameters)
    assert isinstance(weather, ECHistorical)


@pytest.fixture()
def test_historical():
    return ECHistorical(station_id=1096453, year=2021)


@pytest.mark.slow
def test_update(test_historical):
    asyncio.run(test_historical.update())
    assert test_historical.metadata
    assert test_historical.station_data


@pytest.mark.parametrize(
    "station_id,timeframe,startdate,enddate",
    [
        (1096453, "daily", datetime(2022, 1, 1), datetime(2022, 12, 31)),
        ("1096453", "daily", datetime(2022, 1, 1), datetime(2022, 12, 31)),
        ("10476F0", "daily", datetime(2022, 1, 1), datetime(2022, 12, 31)),
        (1096453, "daily", datetime(2022, 1, 1), datetime(2023, 12, 31)),
        (1096453, "daily", datetime(2022, 1, 1), datetime(2022, 11, 30)),
        (1096453, "hourly", datetime(2022, 1, 1), datetime(2022, 2, 3, 23, 59, 59)),
    ],
)
@pytest.mark.slow
def test_historical_number_values(station_id, timeframe, startdate, enddate):
    if timeframe == "daily":
        number_of_data_points_per_day = 1
    else:
        number_of_data_points_per_day = 24
    number_of_data_points = (
        (enddate - startdate).days + 1
    ) * number_of_data_points_per_day
    ec = ECHistoricalRange(
        station_id=station_id, timeframe=timeframe, daterange=(startdate, enddate)
    )
    data = ec.get_data()
    rows, _ = data.shape
    assert rows == number_of_data_points


@pytest.mark.parametrize(
    "init_parameters",
    [
        {"year": 1839},
        {"year": 2021, "month": 13},
        {"year": 2021, "month": 0},
        {"year": 2021, "day": 32},
        {"year": 2021, "language": "spanish"},
        {"year": 2021, "format": "json"},
        {"year": 2021, "timeframe": 3},
        {"year": 2021, "station_id": 1096453.5},
        {"station_id": 1096453},
    ],
    ids=[
        "year before 1840",
        "month 13",
        "month 0",
        "day 32",
        "unknown language",
        "unknown format",
        "monthly timeframe",
        "station id a float",
        "no year",
    ],
)
def test_init_rejects_what_the_service_cannot_answer(init_parameters):
    init_parameters = {"station_id": 1096453, **init_parameters}
    with pytest.raises(vol.Invalid):
        ECHistorical(**init_parameters)


def test_an_integer_station_id_is_sent_as_a_string(climate):
    updated(station_id=1096453)

    assert climate.requests[0]["climate_id"] == "1096453"


def test_daily_xml_is_keyed_by_date(climate):
    weather = updated(format="xml")

    assert list(weather.station_data) == [
        "2021-01-01",
        "2021-01-02",
        "2021-01-03",
        "2021-04-29",
        "2021-04-30",
    ]
    assert weather.station_data["2021-01-01"]["maxtemp"] == {
        "value": 5.6,
        "unit": "°C",
        "label": "Maximum Temperature",
    }
    assert weather.station_data["2021-01-01"]["dirofmaxgust"] == {
        "value": 19,
        "unit": "10s Deg",
        "label": "Direction of Maximum Gust",
    }


def test_daily_xml_reports_an_empty_element_as_none(climate):
    """Total rain is blank on a January day: the element is there, with a
    unit and nothing inside."""
    record = updated(format="xml").station_data["2021-01-01"]

    assert record["totalrain"] == {"value": None, "label": "Total Rain"}


def test_daily_xml_reports_a_day_flagged_missing_as_none(climate):
    """On 29 April the service flags most of the day's values "M" for
    missing and sends them empty; the snow on the ground was still read."""
    record = updated(format="xml").station_data["2021-04-29"]

    assert record["maxtemp"]["value"] is None
    assert record["totalprecipitation"]["value"] is None
    assert record["snowonground"]["value"] == 2.0


def test_french_xml_has_french_labels_and_comma_decimals(climate):
    """The French service writes 5,6 where the English one writes 5.6."""
    record = updated(format="xml", language="french").station_data["2021-01-01"]

    assert record["maxtemp"]["label"] == "Température maximale"
    assert record["maxtemp"]["value"] == 5.6
    assert record["mintemp"]["value"] == -4.7


def test_xml_metadata_describes_the_station(climate):
    assert updated(format="xml").metadata == {
        "name": "PRINCE GEORGE AIRPORT AUTO",
        "province": "BRITISH COLUMBIA",
        "stationoperator": (
            "Environment and Climate Change Canada - Meteorological Service of Canada"
        ),
        "latitude": "53.89",
        "longitude": "-122.67",
        "elevation": "680.00",
        "climate_identifier": "1096453",
        "wmo_identifier": "71302",
        "tc_identifier": "VXS",
    }


def test_xml_without_a_byte_order_mark_is_read(climate):
    """The service's XML begins with a byte-order mark. The parser was handed
    text, which lxml refuses when it opens with an encoding declaration -
    and only the mark, by putting something before the declaration, kept it
    from noticing. A response without the mark raised ValueError. Synthetic:
    the captured response without its mark."""
    body = (FIXTURES / "daily_en.xml").read_bytes().removeprefix(b"\xef\xbb\xbf")
    assert body.startswith(b"<?xml")
    climate.override = (body, XML)
    weather = updated(format="xml")

    assert weather.station_data["2021-01-01"]["maxtemp"]["value"] == 5.6


def test_hourly_xml_is_keyed_by_the_hour(climate):
    """Hourly records carry an hour and a minute and different elements from
    daily ones. They were read as daily records - keyed by date alone, so 744
    hours collapsed to the 31 days of the month, and looked up by element
    names an hourly record doesn't have, so every value came back None."""
    weather = updated(format="xml", month=5, timeframe=1)

    assert len(weather.station_data) == 26
    assert list(weather.station_data)[:2] == ["2021-05-01 00:00", "2021-05-01 01:00"]
    assert list(weather.station_data)[-1] == "2021-05-02 01:00"
    assert weather.station_data["2021-05-01 00:00"]["temp"] == {
        "value": 4.7,
        "unit": "°C",
        "label": "Temperature",
    }
    assert weather.station_data["2021-05-01 00:00"]["relhum"]["value"] == 76
    assert weather.station_data["2021-05-01 00:00"]["weather"]["value"] == "NA"


def test_hourly_xml_reports_a_blank_value_as_none(climate):
    """Visibility is empty all month, and the wind direction of the 23:00
    record is a single space rather than empty, which int() rejects."""
    data = updated(format="xml", month=5, timeframe=1).station_data

    assert data["2021-05-01 00:00"]["visibility"]["value"] is None
    assert data["2021-05-01 23:00"]["winddir"]["value"] is None
    assert data["2021-05-01 22:00"]["winddir"]["value"] == 30


def test_hourly_xml_is_labelled_in_the_requested_language(climate):
    data = updated(format="xml", month=5, timeframe=1, language="french").station_data

    label = data["2021-05-01 00:00"]["temp"]["label"]
    assert label != "Temperature"
    assert data["2021-05-01 00:00"]["temp"]["value"] == 4.7


def test_csv_leaves_the_data_to_be_read_as_csv(climate):
    weather = updated(format="csv")

    frame = pd.read_csv(weather.station_data)
    assert len(frame) == 10
    assert frame["Climate ID"].tolist() == [1096453] * 10
    assert frame.loc[0, "Max Temp (°C)"] == 5.6
    assert frame.columns[0] == "Longitude (x)"


def test_csv_metadata_comes_from_the_first_row(climate):
    assert updated(format="csv").metadata == {
        "longitude": "-122.67",
        "latitude": "53.89",
        "name": "PRINCE GEORGE AIRPORT AUTO",
        "climate_identifier": "1096453",
    }


def test_hourly_csv_reads(climate):
    weather = updated(format="csv", month=5, timeframe=1)

    frame = pd.read_csv(weather.station_data)
    assert len(frame) == 8
    assert frame["Date/Time (LST)"].iloc[0] == "2021-05-01 00:00"


@pytest.mark.parametrize("format", ["csv", "xml"])
def test_unknown_station_is_reported_as_one(climate, format):
    """For a climate ID it doesn't have, the service answers 200 with a CSV
    that is only a header, or an XML document that is only a byte-order mark.
    Those raised RuntimeError ("coroutine raised StopIteration") from the
    CSV parser and lxml's XMLSyntaxError from the XML one."""
    weather = historical(station_id=UNKNOWN_STATION, format=format)

    with pytest.raises(ec_exc.UnknownStationId):
        asyncio.run(weather.update())


def test_stations_are_listed_by_name(climate):
    stations = asyncio.run(get_historical_stations((53.9, -122.7), radius=25))

    assert len(stations) == 4
    assert stations["PRINCE GEORGE AIRPORT AUTO"] == {
        "prov": "BC",
        "proximity": 2.21,
        "id": "1096453",
        "hlyRange": "2009-11-27|2026-09-29",
        "dlyRange": "2009-11-27|2026-09-29",
        "mlyRange": "|",
    }


def test_station_search_sends_the_location_and_years(climate):
    asyncio.run(
        get_historical_stations(
            (53.9, -122.7), radius=50, start_year=2020, end_year=2021, limit=10
        )
    )

    sent = climate.requests[0]
    assert (sent["txtLatDecDeg"], sent["txtLongDecDeg"]) == ("53.9", "-122.7")
    assert sent["txtRadius"] == "50"
    assert (sent["StartYear"], sent["EndYear"]) == ("2020", "2021")
    assert sent["selRowPerPage"] == "10"


def test_station_search_ends_at_the_current_year_by_default(climate):
    """The default end year was the year the module was imported in, so a
    process that stayed up over New Year searched a year short."""
    with freeze_time("2031-06-01", real_asyncio=True):
        asyncio.run(get_historical_stations((53.9, -122.7)))

    assert climate.requests[0]["EndYear"] == "2031"


def range_of(start, stop, **kwargs):
    return ECHistoricalRange(station_id=1096453, daterange=(start, stop), **kwargs)


def days(data):
    return [stamp.strftime("%Y-%m-%d") for stamp in data.index]


def hours(data):
    return [stamp.strftime("%Y-%m-%d %H:%M") for stamp in data.index]


def test_daily_range_keeps_only_the_days_asked_for(climate):
    """The service answers with the whole year, so the rows outside the
    range are dropped."""
    data = range_of(datetime(2021, 1, 2), datetime(2021, 1, 5)).get_data()

    assert days(data) == ["2021-01-02", "2021-01-03", "2021-01-04", "2021-01-05"]
    assert data["Max Temp (°C)"].tolist() == [6.5, 1.9, 0.4, 4.2]


def test_daily_range_asks_once_for_each_year_and_joins_them_in_order(climate):
    data = range_of(datetime(2020, 12, 30), datetime(2021, 1, 2)).get_data()

    assert sorted(request["Year"] for request in climate.requests) == ["2020", "2021"]
    assert {request["timeframe"] for request in climate.requests} == {"2"}
    assert days(data) == ["2020-12-30", "2020-12-31", "2021-01-01", "2021-01-02"]


def test_hourly_range_asks_for_each_month_and_joins_them_in_order(climate):
    data = range_of(
        datetime(2021, 4, 30, 22), datetime(2021, 5, 1, 2), timeframe="hourly"
    ).get_data()

    assert sorted(request["Month"] for request in climate.requests) == ["4", "5"]
    assert {request["timeframe"] for request in climate.requests} == {"1"}
    assert hours(data) == [
        "2021-04-30 22:00",
        "2021-04-30 23:00",
        "2021-05-01 00:00",
        "2021-05-01 01:00",
        "2021-05-01 02:00",
    ]
    assert data["Temp (°C)"].tolist() == [6.4, 5.9, 4.7, 2.2, 3.4]


def test_a_range_given_backwards_is_the_same_range(climate):
    """flip_daterange was meant to swap the dates, but tested whether the
    tuple was a name in the module's globals, which it never is. The range
    then held no months at all, and get_data() raised IndexError."""
    backwards = range_of(datetime(2021, 1, 5), datetime(2021, 1, 2))

    assert backwards.startdate == datetime(2021, 1, 2)
    assert backwards.stopdate == datetime(2021, 1, 5)
    assert days(backwards.get_data()) == [
        "2021-01-02",
        "2021-01-03",
        "2021-01-04",
        "2021-01-05",
    ]


def test_a_range_given_as_dates_is_accepted(climate):
    """Comparing the rows' datetimes with a date raises TypeError in pandas,
    so a range of dates has to become one of datetimes."""
    data = range_of(date(2021, 1, 2), date(2021, 1, 5)).get_data()

    assert days(data) == ["2021-01-02", "2021-01-03", "2021-01-04", "2021-01-05"]


def test_a_date_as_the_end_of_an_hourly_range_covers_the_whole_day(climate):
    """A date is midnight, and as a stop that would leave out every hour of
    the last day but the first."""
    data = range_of(date(2021, 5, 1), date(2021, 5, 1), timeframe="hourly").get_data()

    assert len(data) == 8
    assert hours(data)[0] == "2021-05-01 00:00"


def test_the_default_range_is_the_last_year_or_so_up_to_today(climate):
    """The default was worked out when the module was imported, so a process
    that stayed up kept asking for the same stale window. It also was a pair
    of dates, which made get_data() raise TypeError: the documented call
    with no daterange never worked."""
    with freeze_time("2021-02-10"):
        first = ECHistoricalRange(station_id=1096453)
    with freeze_time("2021-03-15"):
        later = ECHistoricalRange(station_id=1096453)

    assert (first.startdate, first.stopdate) == (
        datetime(2020, 1, 1),
        datetime.combine(date(2021, 2, 10), time.max),
    )
    assert (later.startdate, later.stopdate) == (
        datetime(2020, 2, 1),
        datetime.combine(date(2021, 3, 15), time.max),
    )
    data = first.get_data()
    assert days(data)[:5] == [
        "2020-12-28",
        "2020-12-29",
        "2020-12-30",
        "2020-12-31",
        "2021-01-01",
    ]
    assert len(data) == 14


def test_a_french_range_is_numeric(climate):
    """The French service writes 5,6 for 5.6. Read with pandas' defaults every
    number in a French range was left as text, and the decimal comma was
    written out again by the csv property on top of that."""
    data = range_of(
        datetime(2021, 1, 2), datetime(2021, 1, 5), language="french"
    ).get_data()

    assert pd.api.types.is_float_dtype(data["Temp max.(°C)"])
    assert data["Temp max.(°C)"].tolist() == [6.5, 1.9, 0.4, 4.2]


def test_range_renders_as_csv(climate):
    ec = range_of(datetime(2021, 1, 2), datetime(2021, 1, 3))

    lines = ec.csv.splitlines()
    assert len(lines) == 3
    assert lines[0].split(";")[:3] == ["Date/Time", "Longitude (x)", "Latitude (y)"]
    assert lines[1].startswith("2021-01-02;-122.67;53.89;PRINCE GEORGE AIRPORT AUTO;")
    assert ";6.5;" in lines[1]


def test_a_french_range_renders_csv_with_decimal_commas(climate):
    ec = range_of(datetime(2021, 1, 2), datetime(2021, 1, 3), language="french")

    assert ";6,5;" in ec.csv.splitlines()[1]


def test_rendering_again_reuses_the_data_already_fetched(climate):
    ec = range_of(datetime(2021, 1, 2), datetime(2021, 1, 3))
    first = ec.csv
    requests = len(climate.requests)

    assert ec.csv == first
    assert len(climate.requests) == requests


def test_range_renders_as_xml(climate):
    ec = range_of(datetime(2021, 1, 2), datetime(2021, 1, 3))

    xml = ec.xml

    assert xml.count("<row>") == 2
    assert "<Max_Temp_C>6.5</Max_Temp_C>" in xml
    assert "<Date_Time>2021-01-02" in xml


def test_getting_the_data_again_replaces_it(climate):
    ec = range_of(datetime(2021, 1, 2), datetime(2021, 1, 5))
    ec.get_data()
    data = ec.get_data()

    assert len(data) == 4


def test_a_range_for_an_unknown_station_is_reported_as_one(climate):
    ec = ECHistoricalRange(
        station_id=UNKNOWN_STATION,
        daterange=(datetime(2021, 1, 2), datetime(2021, 1, 5)),
    )

    with pytest.raises(ec_exc.UnknownStationId):
        ec.get_data()


@pytest.mark.parametrize(
    "kwargs",
    [{"timeframe": "monthly"}, {"timeframe": "weekly"}, {"language": "spanish"}],
)
def test_range_rejects_what_it_cannot_fetch(kwargs):
    """Both failed only at get_data() - a KeyError for the timeframe, and for
    'monthly', which the class accepted and its docstring promises, a
    vol.Invalid from deep inside - and not when the object was built."""
    with pytest.raises(vol.Invalid):
        range_of(datetime(2021, 1, 2), datetime(2021, 1, 5), **kwargs)


@pytest.mark.asyncio
async def test_a_range_can_be_updated_from_a_running_event_loop(climate):
    """get_data() ran asyncio.run() for each month, which raises when a loop
    is already running - in a notebook, or an async application - so the
    class couldn't be used there at all."""
    ec = range_of(datetime(2021, 1, 2), datetime(2021, 1, 5))
    data = await ec.update()

    assert days(data) == ["2021-01-02", "2021-01-03", "2021-01-04", "2021-01-05"]
    assert ec.df is data


@pytest.mark.asyncio
async def test_get_data_from_a_running_event_loop_points_to_update(climate):
    ec = range_of(datetime(2021, 1, 2), datetime(2021, 1, 5))

    with pytest.raises(RuntimeError, match="update"):
        ec.get_data()
