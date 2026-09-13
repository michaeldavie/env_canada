import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import voluptuous as vol

from env_canada import ECPrecipForecast
from env_canada.ec_cache import Cache
from env_canada.ec_geomet import LayerDimension, parse_iso_duration


@pytest.fixture
def capabilities():
    """GetCapabilities responses keyed by layer, mirroring the real GeoMet
    response shape: a `time` dimension of "start/end/step", plus a
    `reference_time` dimension on the forecast layers identifying the run."""

    def layer(name, time_dim, reference_dim=None, default=None):
        dimensions = f'<Dimension name="time" units="ISO8601" default="{default or time_dim[1]}" nearestValue="0">{time_dim[0]}/{time_dim[1]}/{time_dim[2]}</Dimension>'
        if reference_dim:
            dimensions += f'<Dimension name="reference_time" units="ISO8601" default="{reference_dim}" multipleValues="1" nearestValue="0">2025-02-13T06:00:00Z/{reference_dim}/PT6H</Dimension>'
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<WMS_Capabilities xmlns="http://www.opengis.net/wms">'
            f"<Layer><Name>{name}</Name>{dimensions}</Layer>"
            "</WMS_Capabilities>"
        ).encode()

    return {
        "RADAR_1KM_RRAI": layer(
            "RADAR_1KM_RRAI",
            ("2025-02-13T13:54:00Z", "2025-02-13T16:54:00Z", "PT6M"),
        ),
        "RADAR_1KM_RSNO": layer(
            "RADAR_1KM_RSNO",
            ("2025-02-13T13:54:00Z", "2025-02-13T16:54:00Z", "PT6M"),
        ),
        "Radar_1km_SfcPrecipType": layer(
            "Radar_1km_SfcPrecipType",
            ("2025-02-13T13:54:00Z", "2025-02-13T16:54:00Z", "PT6M"),
        ),
        "Radar_1km_RainPrecipRate-Extrapolation": layer(
            "Radar_1km_RainPrecipRate-Extrapolation",
            ("2025-02-13T16:54:00Z", "2025-02-13T18:00:00Z", "PT6M"),
            "2025-02-13T16:54:00Z",
        ),
        "Radar_1km_SnowPrecipRate-Extrapolation": layer(
            "Radar_1km_SnowPrecipRate-Extrapolation",
            ("2025-02-13T16:54:00Z", "2025-02-13T18:00:00Z", "PT6M"),
            "2025-02-13T16:54:00Z",
        ),
        "HRDPS.CONTINENTAL_PR": layer(
            "HRDPS.CONTINENTAL_PR",
            ("2025-02-13T12:00:00Z", "2025-02-15T12:00:00Z", "PT1H"),
            "2025-02-13T12:00:00Z",
            default="2025-02-13T13:00:00Z",
        ),
        "HRDPS-WEonG_2.5km_Precip-Prob": layer(
            "HRDPS-WEonG_2.5km_Precip-Prob",
            ("2025-02-13T12:00:00Z", "2025-02-15T12:00:00Z", "PT1H"),
            "2025-02-13T12:00:00Z",
            default="2025-02-13T13:00:00Z",
        ),
        "HRDPS-WEonG_2.5km_PrecipCondAmt": layer(
            "HRDPS-WEonG_2.5km_PrecipCondAmt",
            ("2025-02-13T12:00:00Z", "2025-02-15T12:00:00Z", "PT1H"),
            "2025-02-13T12:00:00Z",
            default="2025-02-13T13:00:00Z",
        ),
        "HRDPS-WEonG_2.5km_DominantPrecipType": layer(
            "HRDPS-WEonG_2.5km_DominantPrecipType",
            ("2025-02-13T12:00:00Z", "2025-02-15T12:00:00Z", "PT1H"),
            "2025-02-13T12:00:00Z",
            default="2025-02-13T13:00:00Z",
        ),
    }


def feature_info(value, klass=None):
    """A GetFeatureInfo FeatureCollection, as GeoMet renders it."""
    return json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-100.0, 50.0]},
                    "properties": {"value": value, "class": klass},
                }
            ],
        }
    )


EXCEPTION_XML = """<?xml version='1.0' encoding="utf-8"?>
    <ogc:ServiceExceptionReport version="1.3.0"
    xmlns:ogc="http://www.opengis.net/ogc">
    <ogc:ServiceException code="NoMatch" locator="time">no data</ogc:ServiceException>
    </ogc:ServiceExceptionReport>"""


def make_responder(capabilities, values, captured=None):
    """Build a _get_resource stand-in.

    `values` maps a layer name to either a single response or a dict keyed by
    the requested time.
    """

    async def respond(_url, params, bytes=True):
        if params.get("request") == "GetCapabilities":
            return capabilities[params["layer"]]

        if captured is not None:
            captured.append(params)

        response = values.get(params["layers"])
        if isinstance(response, dict):
            response = response.get(params["time"], EXCEPTION_XML)
        return response if response is not None else EXCEPTION_XML

    return respond


# Dimension parsing


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        ("PT6M", timedelta(minutes=6)),
        ("PT1H", timedelta(hours=1)),
        ("PT3H", timedelta(hours=3)),
        ("P1D", timedelta(days=1)),
        ("PT30S", timedelta(seconds=30)),
        ("P1M", None),  # months aren't a fixed length
        ("nonsense", None),
    ],
)
def test_parse_iso_duration(duration, expected):
    assert parse_iso_duration(duration) == expected


def test_steps_use_the_dimension_grid():
    """GeoMet rejects any time not exactly on the advertised step, so steps
    must be generated from the dimension's own start."""
    dimension = LayerDimension(
        start=datetime(2025, 2, 13, 13, 54, tzinfo=UTC),
        end=datetime(2025, 2, 13, 16, 54, tzinfo=UTC),
        default=None,
        step=timedelta(minutes=6),
    )
    steps = ECPrecipForecast._steps(
        dimension,
        datetime(2025, 2, 13, 16, 31, tzinfo=UTC),  # deliberately off-grid
        datetime(2025, 2, 13, 16, 54, tzinfo=UTC),
        timedelta(minutes=6),
    )
    assert steps == [
        datetime(2025, 2, 13, 16, 36, tzinfo=UTC),
        datetime(2025, 2, 13, 16, 42, tzinfo=UTC),
        datetime(2025, 2, 13, 16, 48, tzinfo=UTC),
        datetime(2025, 2, 13, 16, 54, tzinfo=UTC),
    ]


def test_steps_clamp_to_the_dimension_window():
    dimension = LayerDimension(
        start=datetime(2025, 2, 13, 16, 54, tzinfo=UTC),
        end=datetime(2025, 2, 13, 17, 6, tzinfo=UTC),
        default=None,
        step=timedelta(minutes=6),
    )
    steps = ECPrecipForecast._steps(
        dimension,
        datetime(2025, 2, 13, 12, 0, tzinfo=UTC),
        datetime(2025, 2, 13, 23, 0, tzinfo=UTC),
        timedelta(minutes=6),
    )
    assert steps[0] == dimension.start
    assert steps[-1] == dimension.end


# Initialization


def test_defaults():
    forecast = ECPrecipForecast(coordinates=(50, -100))
    assert forecast.precip_type == "auto"
    assert forecast.past_minutes == 60
    assert forecast.future_minutes == 72
    assert forecast.hourly_hours == 24
    assert forecast.metadata["attribution"] == "Data provided by Environment Canada"


def test_bounding_box_brackets_the_point():
    forecast = ECPrecipForecast(coordinates=(50, -100))
    lat_min, lon_min, lat_max, lon_max = forecast.bbox
    assert lat_min < 50 < lat_max
    assert lon_min < -100 < lon_max


@pytest.mark.parametrize(
    "kwargs",
    [
        {"coordinates": (95, -100)},
        {"coordinates": (50, -200)},
        {"coordinates": (50, -100), "precip_type": "hail"},
        {"coordinates": (50, -100), "hourly_hours": 96},
        {"coordinates": (50, -100), "future_minutes": 600},
        {"coordinates": (50, -100), "language": "spanish"},
    ],
)
def test_invalid_parameters_rejected(kwargs):
    with pytest.raises(vol.Invalid):
        ECPrecipForecast(**kwargs)


def test_french_attribution():
    forecast = ECPrecipForecast(coordinates=(50, -100), language="french")
    assert (
        forecast.metadata["attribution"] == "Données fournies par Environnement Canada"
    )


# Nowcast


@patch("env_canada.ec_precip_forecast._get_resource")
def test_nowcast_spans_observed_and_extrapolation(mock_get_resource, capabilities):
    Cache.clear()
    captured = []
    mock_get_resource.side_effect = make_responder(
        capabilities,
        {
            "Radar_1km_SfcPrecipType": feature_info(310, "Light Rain"),
            "RADAR_1KM_RRAI": feature_info(1.2391, "1.0 - 2.0 (mm/h)"),
            "Radar_1km_RainPrecipRate-Extrapolation": feature_info(
                2.484375, "2.0 - 4.0 (mm/h)"
            ),
        },
        captured,
    )

    forecast = ECPrecipForecast(
        coordinates=(50, -100), past_minutes=18, future_minutes=18, hourly_hours=0
    )
    asyncio.run(forecast.update())

    assert [entry["timestamp"] for entry in forecast.nowcast] == [
        datetime(2025, 2, 13, 16, 36, tzinfo=UTC),
        datetime(2025, 2, 13, 16, 42, tzinfo=UTC),
        datetime(2025, 2, 13, 16, 48, tzinfo=UTC),
        datetime(2025, 2, 13, 16, 54, tzinfo=UTC),
        datetime(2025, 2, 13, 17, 0, tzinfo=UTC),
        datetime(2025, 2, 13, 17, 6, tzinfo=UTC),
        datetime(2025, 2, 13, 17, 12, tzinfo=UTC),
    ]
    # The last observation is "now": everything after it is extrapolated.
    assert [entry["forecast"] for entry in forecast.nowcast] == [
        False,
        False,
        False,
        False,
        True,
        True,
        True,
    ]
    assert forecast.nowcast[0]["rate"] == 1.2391
    assert forecast.nowcast[0]["unit"] == "mm/h"
    assert forecast.nowcast[0]["label"] == "1.0 - 2.0 (mm/h)"
    assert forecast.nowcast[-1]["rate"] == 2.484375
    assert forecast.metadata["timestamp"] == "2025-02-13T16:54:00+00:00"

    # Every forecast request is pinned to a single model run.
    reference_times = {
        params.get("dim_reference_time")
        for params in captured
        if params["layers"].endswith("Extrapolation")
    }
    assert reference_times == {"2025-02-13T16:54:00Z"}


@patch("env_canada.ec_precip_forecast._get_resource")
def test_nowcast_leaves_gap_when_extrapolation_opens_late(
    mock_get_resource, capabilities
):
    """The observed and extrapolation layers refresh independently, so the
    extrapolation window can open after the last observation. The gap is left
    empty rather than filled with a value belonging to another time."""
    Cache.clear()
    capabilities["Radar_1km_RainPrecipRate-Extrapolation"] = capabilities[
        "Radar_1km_RainPrecipRate-Extrapolation"
    ].replace(
        b"2025-02-13T16:54:00Z/2025-02-13T18:00:00Z",
        b"2025-02-13T17:12:00Z/2025-02-13T18:00:00Z",
    )

    mock_get_resource.side_effect = make_responder(
        capabilities,
        {
            "Radar_1km_SfcPrecipType": feature_info(310, "Light Rain"),
            "RADAR_1KM_RRAI": feature_info(1.0, "1.0 - 2.0 (mm/h)"),
            "Radar_1km_RainPrecipRate-Extrapolation": feature_info(
                2.0, "2.0 - 4.0 (mm/h)"
            ),
        },
    )

    forecast = ECPrecipForecast(
        coordinates=(50, -100), past_minutes=0, future_minutes=24, hourly_hours=0
    )
    asyncio.run(forecast.update())

    forecast_times = [entry["timestamp"] for entry in forecast.nowcast]
    assert datetime(2025, 2, 13, 17, 0, tzinfo=UTC) not in forecast_times
    assert forecast_times[0] == datetime(2025, 2, 13, 17, 12, tzinfo=UTC)


@patch("env_canada.ec_precip_forecast._get_resource")
def test_service_exception_skips_the_entry(mock_get_resource, capabilities):
    """GeoMet advertises a continuous time range but answers a gap with a
    ServiceExceptionReport - XML, at HTTP 200."""
    Cache.clear()
    mock_get_resource.side_effect = make_responder(
        capabilities,
        {
            "Radar_1km_SfcPrecipType": feature_info(310, "Light Rain"),
            "RADAR_1KM_RRAI": {
                "2025-02-13T16:48:00Z": feature_info(1.0, "1.0 - 2.0 (mm/h)"),
                "2025-02-13T16:54:00Z": EXCEPTION_XML,
            },
        },
    )

    forecast = ECPrecipForecast(
        coordinates=(50, -100), past_minutes=6, future_minutes=0, hourly_hours=0
    )
    asyncio.run(forecast.update())

    assert [entry["timestamp"] for entry in forecast.nowcast] == [
        datetime(2025, 2, 13, 16, 48, tzinfo=UTC)
    ]


@pytest.mark.parametrize(
    ("label", "expected_layer", "expected_unit"),
    [
        ("Light Rain", "RADAR_1KM_RRAI", "mm/h"),
        ("Heavy Snow", "RADAR_1KM_RSNO", "cm/h"),
        ("Ice Pellets", "RADAR_1KM_RSNO", "cm/h"),
    ],
)
@patch("env_canada.ec_precip_forecast._get_resource")
def test_auto_precip_type_follows_radar(
    mock_get_resource, capabilities, label, expected_layer, expected_unit
):
    Cache.clear()
    captured = []
    mock_get_resource.side_effect = make_responder(
        capabilities,
        {
            "Radar_1km_SfcPrecipType": feature_info(310, label),
            "RADAR_1KM_RRAI": feature_info(1.0, "1.0 - 2.0 (mm/h)"),
            "RADAR_1KM_RSNO": feature_info(1.0, "1.0 - 2.0 (cm/h)"),
        },
        captured,
    )

    forecast = ECPrecipForecast(
        coordinates=(50, -100), past_minutes=6, future_minutes=0, hourly_hours=0
    )
    asyncio.run(forecast.update())

    assert expected_layer in {params["layers"] for params in captured}
    assert forecast.nowcast[0]["unit"] == expected_unit


@patch("env_canada.ec_precip_forecast._get_resource")
def test_auto_precip_type_falls_back_to_season(mock_get_resource, capabilities):
    """Radar reports no precipitation type over a clear sky, so the season
    decides which rate layer to read."""
    Cache.clear()
    captured = []
    mock_get_resource.side_effect = make_responder(
        capabilities,
        {
            "Radar_1km_SfcPrecipType": feature_info(0, "Undetected"),
            "RADAR_1KM_RRAI": feature_info(0.0, "Undetected"),
            "RADAR_1KM_RSNO": feature_info(0.0, "Undetected"),
        },
        captured,
    )

    with patch("env_canada.ec_precip_forecast.date") as mock_date:
        mock_date.today.return_value = datetime(2025, 7, 1).date()
        forecast = ECPrecipForecast(
            coordinates=(50, -100), past_minutes=6, future_minutes=0, hourly_hours=0
        )
        asyncio.run(forecast.update())

    assert forecast.metadata["precip_type_resolved"] == "rain"
    assert "RADAR_1KM_RRAI" in {params["layers"] for params in captured}


@patch("env_canada.ec_precip_forecast._get_resource")
def test_explicit_precip_type_skips_the_radar_lookup(mock_get_resource, capabilities):
    Cache.clear()
    captured = []
    mock_get_resource.side_effect = make_responder(
        capabilities,
        {"RADAR_1KM_RSNO": feature_info(1.0, "1.0 - 2.0 (cm/h)")},
        captured,
    )

    forecast = ECPrecipForecast(
        coordinates=(50, -100),
        precip_type="snow",
        past_minutes=6,
        future_minutes=0,
        hourly_hours=0,
    )
    asyncio.run(forecast.update())

    assert "Radar_1km_SfcPrecipType" not in {params["layers"] for params in captured}
    assert forecast.nowcast[0]["precip_type"] == "snow"


# Hourly


def hourly_responder(capabilities, accumulations, captured=None, **overrides):
    """Responder whose HRDPS.CONTINENTAL_PR values follow `accumulations`,
    a mapping of hour-of-day to the run-cumulative total at that hour."""
    values = {
        "HRDPS.CONTINENTAL_PR": {
            f"2025-02-13T{hour:02d}:00:00Z": feature_info(total, "0.1 - 2.0")
            for hour, total in accumulations.items()
        },
        "HRDPS-WEonG_2.5km_Precip-Prob": feature_info(52, "50 - 60"),
        "HRDPS-WEonG_2.5km_PrecipCondAmt": feature_info(0.000645, "0.5 - 1 mm"),
        "HRDPS-WEonG_2.5km_DominantPrecipType": feature_info(101, "Rain"),
    }
    values.update(overrides)
    return make_responder(capabilities, values, captured)


@patch("env_canada.ec_precip_forecast._get_resource")
def test_hourly_differences_cumulative_accumulation(mock_get_resource, capabilities):
    """HRDPS.CONTINENTAL_PR accumulates from the start of the run, so each
    hour's amount is the difference between adjacent steps."""
    Cache.clear()
    mock_get_resource.side_effect = hourly_responder(
        capabilities, {12: 0.5, 13: 0.75, 14: 2.5, 15: 2.5, 16: 4.0}
    )

    forecast = ECPrecipForecast(
        coordinates=(50, -100), past_minutes=0, future_minutes=0, hourly_hours=3
    )
    asyncio.run(forecast.update())

    assert [entry["timestamp"].hour for entry in forecast.hourly] == [13, 14, 15, 16]
    assert [entry["amount"] for entry in forecast.hourly] == [0.25, 1.75, 0.0, 1.5]


@patch("env_canada.ec_precip_forecast._get_resource")
def test_hourly_first_step_has_no_baseline(mock_get_resource, capabilities):
    """The layer's first published step is itself the first hour's
    accumulation, so the missing baseline before it counts as zero."""
    Cache.clear()
    # Open the window at the anchor, leaving nothing before it to difference.
    capabilities["HRDPS.CONTINENTAL_PR"] = capabilities["HRDPS.CONTINENTAL_PR"].replace(
        b">2025-02-13T12:00:00Z/", b">2025-02-13T13:00:00Z/"
    )
    mock_get_resource.side_effect = hourly_responder(capabilities, {13: 0.75, 14: 2.5})

    forecast = ECPrecipForecast(
        coordinates=(50, -100), past_minutes=0, future_minutes=0, hourly_hours=1
    )
    asyncio.run(forecast.update())

    assert forecast.hourly[0]["amount"] == 0.75


@patch("env_canada.ec_precip_forecast._get_resource")
def test_hourly_clamps_negative_differences(mock_get_resource, capabilities):
    """Adjacent steps of a cumulative field can differ by a hair in the wrong
    direction purely from storage precision."""
    Cache.clear()
    mock_get_resource.side_effect = hourly_responder(
        capabilities, {12: 1.0, 13: 0.9999999}
    )

    forecast = ECPrecipForecast(
        coordinates=(50, -100), past_minutes=0, future_minutes=0, hourly_hours=1
    )
    asyncio.run(forecast.update())

    assert forecast.hourly[0]["amount"] == 0.0


@patch("env_canada.ec_precip_forecast._get_resource")
def test_hourly_converts_conditional_amount_to_millimetres(
    mock_get_resource, capabilities
):
    """PrecipCondAmt is published in metres."""
    Cache.clear()
    mock_get_resource.side_effect = hourly_responder(capabilities, {12: 0.0, 13: 0.0})

    forecast = ECPrecipForecast(
        coordinates=(50, -100), past_minutes=0, future_minutes=0, hourly_hours=1
    )
    asyncio.run(forecast.update())

    entry = forecast.hourly[0]
    assert entry["conditional_amount"] == 0.645
    assert entry["probability"] == 52
    assert entry["expected_amount"] == round(0.52 * 0.645, 3)
    assert entry["precip_type"] == "Rain"
    assert entry["label"] == "0.5 - 1 mm"


@patch("env_canada.ec_precip_forecast._get_resource")
def test_hourly_pins_every_request_to_one_run(mock_get_resource, capabilities):
    Cache.clear()
    captured = []
    mock_get_resource.side_effect = hourly_responder(
        capabilities, {12: 0.0, 13: 0.5, 14: 1.0}, captured
    )

    forecast = ECPrecipForecast(
        coordinates=(50, -100), past_minutes=0, future_minutes=0, hourly_hours=2
    )
    asyncio.run(forecast.update())

    assert forecast.metadata["hourly_reference_time"] == "2025-02-13T12:00:00Z"
    assert {params.get("dim_reference_time") for params in captured} == {
        "2025-02-13T12:00:00Z"
    }


@patch("env_canada.ec_precip_forecast._get_resource")
def test_hourly_disabled(mock_get_resource, capabilities):
    Cache.clear()
    captured = []
    mock_get_resource.side_effect = make_responder(
        capabilities,
        {
            "Radar_1km_SfcPrecipType": feature_info(310, "Light Rain"),
            "RADAR_1KM_RRAI": feature_info(1.0, "1.0 - 2.0 (mm/h)"),
        },
        captured,
    )

    forecast = ECPrecipForecast(
        coordinates=(50, -100), past_minutes=6, future_minutes=0, hourly_hours=0
    )
    asyncio.run(forecast.update())

    assert forecast.hourly == []
    assert not any("HRDPS" in params["layers"] for params in captured)


# Caching


@patch("env_canada.ec_precip_forecast._get_resource")
def test_repeat_update_is_served_from_cache(mock_get_resource, capabilities):
    Cache.clear()
    captured = []
    mock_get_resource.side_effect = make_responder(
        capabilities,
        {
            "Radar_1km_SfcPrecipType": feature_info(310, "Light Rain"),
            "RADAR_1KM_RRAI": feature_info(1.0, "1.0 - 2.0 (mm/h)"),
        },
        captured,
    )

    forecast = ECPrecipForecast(
        coordinates=(50, -100), past_minutes=18, future_minutes=0, hourly_hours=0
    )
    asyncio.run(forecast.update())
    first = len(captured)
    asyncio.run(forecast.update())

    assert len(captured) == first
    assert forecast.clear_cache() > 0


# Live network


@pytest.mark.slow
def test_live_update():
    Cache.clear()
    forecast = ECPrecipForecast(coordinates=(45.42, -75.70), hourly_hours=6)
    asyncio.run(forecast.update())

    assert forecast.nowcast
    assert forecast.hourly

    for series in (forecast.nowcast, forecast.hourly):
        timestamps = [entry["timestamp"] for entry in series]
        assert timestamps == sorted(timestamps)
        assert len(timestamps) == len(set(timestamps))

    assert all(entry["rate"] >= 0 for entry in forecast.nowcast)
    assert all(entry["amount"] >= 0 for entry in forecast.hourly)
    assert all(
        entry["probability"] is None or 0 <= entry["probability"] <= 100
        for entry in forecast.hourly
    )
