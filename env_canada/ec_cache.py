from aiohttp import ClientSession
from datetime import datetime, timedelta

from .constants import USER_AGENT

CACHE_EXPIRE_TIME = timedelta(minutes=200)  # Time is tuned for 3h radar image


class Resource:
    _cache = {}

    @classmethod
    def add_to_cache(cls, cache_key, item, cache_time=CACHE_EXPIRE_TIME):
        cls._cache[cache_key] = (datetime.now() + cache_time, item)
        print(f"Caching: {cache_key}")
        return item

    @classmethod
    def get_from_cache(cls, cache_key):
        def flush_cache():
            """Flush expired cache entries."""

            now = datetime.now()
            expired = [key for key, value in cls._cache.items() if value[0] < now]
            for key in expired:
                # cls._cache[key] = None
                print(f"Flushing: {key}")
                del cls._cache[key]

        flush_cache()  # Flush at start so we don't use expired entries

        result = cls._cache.get(cache_key)
        print(f"Cache get: {cache_key} {"Found" if result else "NOT found"}")
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
