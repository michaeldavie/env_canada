"""Tests for the shared GeoMet WMS plumbing."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from env_canada.ec_cache import Cache
from env_canada.ec_geomet import (
    DEFAULT_CAPABILITIES_CACHE_TIME,
    MAX_CAPABILITIES_CACHE_TIME,
    LayerDimension,
    get_layer_dimension,
)


def capabilities(layer, time_dim):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <WMS_Capabilities xmlns="http://www.opengis.net/wms">
        <Layer>
            <Name>{layer}</Name>
            <Dimension name="time" units="ISO8601">{time_dim}</Dimension>
        </Layer>
    </WMS_Capabilities>""".encode()


def dimension(start, end, step, fetched_at):
    return LayerDimension(
        start=datetime.fromisoformat(start),
        end=datetime.fromisoformat(end),
        default=None,
        step=step,
        fetched_at=datetime.fromisoformat(fetched_at),
    )


class TestEffectiveStart:
    """GeoMet advertises a dimension as a range, not as a list of the
    timestamps it holds, and for radar that range is a fixed-width window
    that slides. A capabilities response held across a publication therefore
    names a start the server no longer serves.

    The window is 13:54Z-16:54Z on a 6-minute grid throughout, so the grid
    instants after it are 17:00Z, 17:06Z and so on.
    """

    @pytest.mark.parametrize(
        ("read_at", "now", "expected"),
        [
            # Read and used within the same step: nothing has been published.
            ("16:56", "16:59", "2025-02-13T13:54:00+00:00"),
            # 17:00Z has gone by, so the window has moved on a step.
            ("16:56", "17:01", "2025-02-13T14:00:00+00:00"),
            # 17:00Z and 17:06Z: two.
            ("16:56", "17:07", "2025-02-13T14:06:00+00:00"),
            # Read right at a grid instant, used just after: the instant
            # itself isn't a crossing.
            ("17:00", "17:03", "2025-02-13T13:54:00+00:00"),
        ],
    )
    def test_start_advances_once_per_grid_instant_crossed(self, read_at, now, expected):
        with freeze_time(f"2025-02-13T{now}:00Z"):
            dim = dimension(
                "2025-02-13T13:54:00+00:00",
                "2025-02-13T16:54:00+00:00",
                timedelta(minutes=6),
                f"2025-02-13T{read_at}:00+00:00",
            )
            assert dim.effective_start == datetime.fromisoformat(expected)

    def test_start_never_advances_past_the_end(self):
        read_at = datetime(2025, 2, 13, 17, 0, tzinfo=UTC)
        with freeze_time(read_at + timedelta(days=1)):
            dim = dimension(
                "2025-02-13T13:54:00+00:00",
                "2025-02-13T16:54:00+00:00",
                timedelta(minutes=6),
                read_at.isoformat(),
            )
            assert dim.effective_start == dim.end

    def test_start_is_unchanged_when_no_step_is_advertised(self):
        read_at = datetime(2025, 2, 13, 17, 0, tzinfo=UTC)
        with freeze_time(read_at + timedelta(hours=3)):
            dim = dimension(
                "2025-02-13T13:54:00+00:00",
                "2025-02-13T16:54:00+00:00",
                None,
                read_at.isoformat(),
            )
            assert dim.effective_start == dim.start

    def test_forecast_window_reaching_into_the_future_is_left_alone(self):
        """A forecast layer's window extends past now and doesn't slide on
        the observed layers' cadence, so a fresh read of one is untouched."""
        read_at = datetime(2025, 2, 13, 17, 0, tzinfo=UTC)
        with freeze_time(read_at + timedelta(minutes=2)):
            dim = dimension(
                "2025-02-13T17:00:00+00:00",
                "2025-02-13T18:00:00+00:00",
                timedelta(minutes=6),
                read_at.isoformat(),
            )
            assert dim.effective_start == dim.start


class TestCapabilitiesCaching:
    """The response is worth re-reading about once per publication, so the
    cache time comes from the cadence the layer itself advertises."""

    @pytest.mark.parametrize(
        ("time_dim", "expected"),
        [
            (
                "2025-02-13T13:54:00Z/2025-02-13T16:54:00Z/PT6M",
                timedelta(minutes=6),
            ),
            (
                "2025-02-13T13:54:00Z/2025-02-13T16:54:00Z/PT10M",
                timedelta(minutes=10),
            ),
            # HRDPS advertises PT1H; hold it no longer than the ceiling.
            (
                "2025-02-13T13:00:00Z/2025-02-15T12:00:00Z/PT1H",
                MAX_CAPABILITIES_CACHE_TIME,
            ),
            # No step advertised at all.
            (
                "2025-02-13T13:54:00Z/2025-02-13T16:54:00Z",
                DEFAULT_CAPABILITIES_CACHE_TIME,
            ),
        ],
    )
    def test_cache_time_follows_the_advertised_step(self, time_dim, expected):
        Cache.clear()
        xml = capabilities("TEST_LAYER", time_dim)

        async def fetch(url, params, bytes=True):
            return xml

        with patch("env_canada.ec_geomet.Cache.add", wraps=Cache.add) as add:
            asyncio.run(get_layer_dimension("TEST_LAYER", fetch=fetch))

        assert add.call_args.args[2] == expected

    def test_response_is_reused_and_carries_its_read_time(self):
        """A second lookup is served from cache, and reports when the
        response was read rather than when it was re-read - otherwise a
        stale window would look permanently fresh."""
        Cache.clear()
        xml = capabilities(
            "TEST_LAYER", "2025-02-13T13:54:00Z/2025-02-13T16:54:00Z/PT6M"
        )
        calls = []

        async def fetch(url, params, bytes=True):
            calls.append(params)
            return xml

        read_at = datetime(2025, 2, 13, 17, 0, tzinfo=UTC)
        with freeze_time(read_at) as frozen:
            first = asyncio.run(get_layer_dimension("TEST_LAYER", fetch=fetch))
            frozen.tick(timedelta(minutes=4))
            second = asyncio.run(get_layer_dimension("TEST_LAYER", fetch=fetch))

        assert len(calls) == 1
        assert first.fetched_at == read_at
        assert second.fetched_at == read_at
