import pytest
import voluptuous as vol

from env_canada import (
    ECAirQuality,
    ECHydro,
    ECMap,
    ECPrecipForecast,
    ECRadar,
    ECWeather,
)
from env_canada.ec_validate import coordinates

# Classes taking coordinates, and whether the parameter is required.
COORDINATE_CLASSES = [ECAirQuality, ECHydro, ECMap, ECPrecipForecast, ECWeather]


@pytest.mark.parametrize(
    "value",
    [
        (45.42, -75.70),
        (0, 0),
        (90, 180),
        (-90, -180),
        (90, -100),  # the pole is a valid latitude
    ],
)
def test_valid_coordinates_accepted(value):
    assert coordinates(value) == tuple(value)


@pytest.mark.parametrize(
    "value",
    [
        (95, -100),  # latitude out of range
        (-95, -100),
        (50, 181),  # longitude out of range
        (50, -181),
        (-100, 50),  # latitude and longitude the wrong way round
        ("45", "-75"),
        (45.42, None),
        (True, False),
        (45.42,),
        (45.42, -75.70, 100),
        45.42,
        None,
    ],
)
def test_invalid_coordinates_rejected(value):
    with pytest.raises(vol.Invalid):
        coordinates(value)


@pytest.mark.parametrize("cls", COORDINATE_CLASSES)
def test_classes_reject_out_of_range_latitude(cls):
    """A latitude of 95 used to slip through: voluptuous reads a tuple schema
    as "any element validator matches" rather than as positional, so 95
    matched the longitude range."""
    with pytest.raises(vol.Invalid):
        cls(coordinates=(95, -100))


@pytest.mark.parametrize("cls", COORDINATE_CLASSES)
def test_classes_reject_swapped_coordinates(cls):
    with pytest.raises(vol.Invalid):
        cls(coordinates=(-100, 45))


@pytest.mark.parametrize("cls", COORDINATE_CLASSES)
def test_classes_accept_valid_coordinates(cls):
    assert cls(coordinates=(45.42, -75.70)) is not None


def test_radar_rejects_out_of_range_latitude():
    """ECRadar validates through the ECMap it wraps."""
    with pytest.raises(vol.Invalid):
        ECRadar(coordinates=(95, -100))
