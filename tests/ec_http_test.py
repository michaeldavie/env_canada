import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import (
    ClientError,
    ClientResponseError,
    ClientSession,
    ClientTimeout,
    ConnectionTimeoutError,
    web,
)
from aiohttp.test_utils import TestServer

from env_canada import ec_hydro
from env_canada.constants import CLIENT_TIMEOUT
from env_canada.ec_http import CONNECT_ATTEMPTS, get_with_retry

STALL = ConnectionTimeoutError("Connection timeout to host https://example.test/")


def scripted(*outcomes):
    """A stand-in for ClientSession.get that, call by call, raises the
    exceptions among `outcomes` and returns the rest."""
    remaining = list(outcomes)

    async def get(self, url, **kwargs):
        outcome = remaining.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return patch.object(ClientSession, "get", get)


def test_a_stalled_connection_is_abandoned_well_before_the_request_limit():
    """From a GitHub runner about one new connection in forty spends close to
    twenty seconds in the TCP connect (19.4 s in one curl run, 19.8 s in an
    aiohttp one), then completes and is answered in milliseconds. Healthy
    connections take 30 ms. With only a 10-second limit on the whole request,
    such a stall failed it; a bound on the connect alone fails it in seconds,
    leaving time to try again."""
    assert CLIENT_TIMEOUT.sock_connect is not None
    assert CLIENT_TIMEOUT.sock_connect < CLIENT_TIMEOUT.total


@pytest.mark.asyncio
async def test_a_stalled_connection_is_retried_and_the_answer_returned():
    answer = object()
    with scripted(STALL, STALL, answer):
        async with ClientSession() as session:
            response = await get_with_retry(session, "https://example.test/")

    assert response is answer


@pytest.mark.asyncio
async def test_a_connection_that_keeps_stalling_is_eventually_reported():
    """The error that comes out is still a TimeoutError, which callers such
    as ECWeather catch, and a ClientError, which others do."""
    with scripted(*[STALL] * CONNECT_ATTEMPTS):
        async with ClientSession() as session:
            with pytest.raises(ConnectionTimeoutError) as raised:
                await get_with_retry(session, "https://example.test/")

    assert isinstance(raised.value, TimeoutError)
    assert isinstance(raised.value, ClientError)


class Counting:
    """A local server that counts the requests it gets, and answers each one
    with `handler`."""

    def __init__(self, handler):
        self.hits = 0
        self.handler = handler
        self.server = TestServer(self._app())

    def _app(self):
        async def serve(request):
            self.hits += 1
            return await self.handler(request)

        app = web.Application()
        app.router.add_get("/", serve)
        return app

    async def __aenter__(self):
        await self.server.start_server()
        self.url = str(self.server.make_url("/"))
        return self

    async def __aexit__(self, *exc):
        await self.server.close()


@pytest.mark.asyncio
async def test_a_server_that_never_answers_is_not_asked_again():
    """Only a connection that could not be made is worth another try. A
    server that accepted the request and then said nothing is slow or
    overloaded, and asking it twice more would not help it."""
    release = asyncio.Event()

    async def never_answers(request):
        await release.wait()
        return web.Response()

    async with Counting(never_answers) as service:
        start = time.monotonic()
        try:
            async with ClientSession() as session:
                with pytest.raises(TimeoutError):
                    await get_with_retry(
                        session, service.url, timeout=ClientTimeout(total=0.3)
                    )
        finally:
            release.set()

        assert time.monotonic() - start < 3
        assert service.hits == 1


@pytest.mark.asyncio
async def test_an_error_response_is_not_retried():
    async def fails(request):
        return web.Response(status=503)

    async with Counting(fails) as service:
        async with ClientSession(raise_for_status=True) as session:
            with pytest.raises(ClientResponseError) as raised:
                await get_with_retry(session, service.url)

        assert raised.value.status == 503
        assert service.hits == 1


@pytest.mark.asyncio
async def test_a_healthy_connection_is_used_once_and_its_body_read():
    async def answers(request):
        return web.Response(body=b"hello")

    async with Counting(answers) as service:
        async with ClientSession(raise_for_status=True) as session:
            response = await get_with_retry(session, service.url)
            assert await response.read() == b"hello"

        assert service.hits == 1


def test_a_module_recovers_from_a_stalled_connection():
    """Through a real call - the hydrometric station list - rather than the
    helper alone: the first connection stalls, the second is answered."""
    sites = (
        Path(__file__).parent / "fixtures" / "hydro" / "station_list.csv"
    ).read_bytes()
    answer = AsyncMock()
    answer.read.return_value = sites

    with scripted(STALL, answer):
        found = asyncio.run(ec_hydro.get_hydro_sites())

    assert {site["ID"] for site in found} >= {"02KF005", "05RE001"}
