import logging

from aiohttp import ClientResponse, ClientSession, ConnectionTimeoutError

LOG = logging.getLogger(__name__)

# How many times a connection is tried before the failure is reported
CONNECT_ATTEMPTS = 3


async def get_with_retry(session: ClientSession, url: str, **kwargs) -> ClientResponse:
    """`session.get`, tried again when the connection can't be made in time.

    Environment Canada's servers occasionally leave a new connection hanging
    for about twenty seconds before accepting it, and answer promptly once
    they do. The library's timeout gives up on a connection long before that,
    and a connection tried again almost always succeeds at once. Only the
    connecting is retried: a server that accepted the request and then
    stalled, or answered with an error, is reported as it is.
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
