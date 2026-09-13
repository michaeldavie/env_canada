"""Shared parameter validators."""

import voluptuous as vol

__all__ = ["coordinates"]


def coordinates(value):
    """Validate a (latitude, longitude) pair.

    Voluptuous reads a tuple schema as "every element matches any one of these
    validators" rather than as positional, so a schema of
    (Range(-90, 90), Range(-180, 180)) accepts a latitude of 95 - it matches
    the longitude validator. The ranges have to be checked by hand for a
    latitude that is out of range, or a latitude/longitude pair given the
    wrong way round, to be caught.
    """
    try:
        latitude, longitude = value
    except (TypeError, ValueError) as err:
        raise vol.Invalid("coordinates must be a (latitude, longitude) pair") from err

    # bool is an int subclass, and (True, False) is not a coordinate.
    for name, coordinate, limit in (
        ("latitude", latitude, 90),
        ("longitude", longitude, 180),
    ):
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
            raise vol.Invalid(f"{name} must be a number")
        if not -limit <= coordinate <= limit:
            raise vol.Invalid(f"{name} must be between -{limit} and {limit}")

    return (latitude, longitude)
