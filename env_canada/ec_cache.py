from aiohttp import ClientSession
from datetime import datetime, timedelta

from .constants import USER_AGENT

CACHE_EXPIRE_TIME = timedelta(minutes=200)  # Time is tuned for 3h radar image

_cache = {}


async def cache_get(url, params, bytes=True, cache_time=CACHE_EXPIRE_TIME):
    """Thin wrapper around ClientSession.get to cache responses."""

    def _flush_cache():
        """Flush expired cache entries."""

        now = datetime.now()
        expired = [key for key, value in _cache.items() if value[0] < now]
        for key in expired:
            # _cache[key] = None
            del _cache[key]

    async def get():
        async with ClientSession(raise_for_status=True) as session:
            response = await session.get(
                url=url, params=params, headers={"User-Agent": USER_AGENT}
            )
            if bytes:
                return await response.read()
            return await response.text()

    _flush_cache()  # Flush at start so we don't use expired entries

    cache_key = (url, tuple(sorted(params.items())))
    result = _cache.get(cache_key)
    if not result:
        result = (
            datetime.now() + cache_time,
            await get(),
        )
        _cache[cache_key] = result

    return result[1]


# class CacheClientSession(ClientSession):
#     """Shim to cache ClientSession requests."""
#
#     _cache = {}
#
#     def _flush_cache(self):
#         """Flush expired cache entries."""
#
#         now = datetime.now()
#         expired = [key for key, value in self._cache.items() if value[0] < now]
#         for key in expired:
#             del self._cache[key]
#
#     async def get(self, url, params, cache_time=CACHE_EXPIRE_TIME):
#         """Thin wrapper around ClientSession.get to cache responses."""
#
#         self._flush_cache()  # Flush at start so we don't use expired entries
#
#         cache_key = (url, tuple(sorted(params.items())))
#         result = self._cache.get(cache_key)
#         if not result:
#             result = (
#                 datetime.now() + cache_time,
#                 await super().get(
#                     url=url, params=params, headers={"User-Agent": USER_AGENT}
#                 ),
#             )
#             self._cache[cache_key] = result
#
#         return result[1]
