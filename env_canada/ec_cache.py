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


class Resource:
    _cache = {}

    @classmethod
    def add_to_cache(cls, cache_key, item, cache_time=CACHE_EXPIRE_TIME):
        cls._cache[cache_key] = (datetime.now() + cache_time, item)
        return item

    @classmethod
    def get_from_cache(cls, cache_key):
        def flush_cache():
            """Flush expired cache entries."""

            now = datetime.now()
            expired = [key for key, value in cls._cache.items() if value[0] < now]
            for key in expired:
                # cls._cache[key] = None
                del cls._cache[key]

        flush_cache()  # Flush at start so we don't use expired entries

        result = cls._cache.get(cache_key)
        return result[1] if result else None

    @classmethod
    async def get(cls, url, params, bytes=True):
        async with ClientSession(raise_for_status=True) as session:
            response = await session.get(
                url=url, params=params, headers={"User-Agent": USER_AGENT}
            )
            if bytes:
                return await response.read()
            return await response.text()
