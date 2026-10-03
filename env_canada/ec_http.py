import logging

from aiohttp import ClientResponse, ClientSession, ConnectionTimeoutError

LOG = logging.getLogger(__name__)

# How many times a connection is tried before the failure is reported
CONNECT_ATTEMPTS = 3


async def get_with_retry(session: ClientSession, url: str, **kwargs) -> ClientResponse:
    """`session.get`, tried again when the connection can't be made in time.

    A new connection to Environment Canada's servers sometimes sits in the
    connect for seconds, or over a minute, before it is accepted, and is
    answered promptly once it is. The library's timeout gives up on such a
    connection early, and a fresh one often succeeds at once. That only
    mitigates it: the stalls come in runs, so all the attempts can stall
    together. Only the connecting is retried: a server that accepted the
    request and then stalled, or answered with an error, is reported as it is.
    """
    for attempt in range(1, CONNECT_ATTEMPTS):
        try:
            return await session.get(url, **kwargs)
        except ConnectionTimeoutError:
            LOG.debug(
                "Connecting to %s timed out (attempt %d of %d)",
                url,
                attempt,
                CONNECT_ATTEMPTS,
            )
    return await session.get(url, **kwargs)
