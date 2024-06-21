from aiohttp import ClientSession
from datetime import datetime, timedelta

from .constants import USER_AGENT

CACHE_EXPIRE_TIME = timedelta(minutes=200)  # Time is tuned for 3h radar image


class CacheClientSession(ClientSession):
    """Shim to cache ClientSession requests."""

    _cache = {}

    def _flush_cache(self):
        """Flush expired cache entries."""

        now = datetime.now()
        expired = [key for key, value in self._cache.items() if value[0] < now]
        for key in expired:
            del self._cache[key]

    async def get(self, url, params, cache_time=CACHE_EXPIRE_TIME):
        """Thin wrapper around ClientSession.get to cache responses."""

        self._flush_cache()  # Flush at start so we don't use expired entries

        cache_key = (url, tuple(sorted(params.items())))
        result = self._cache.get(cache_key)
        if not result:
            result = (
                datetime.now() + cache_time,
                await super().get(
                    url=url, params=params, headers={"User-Agent": USER_AGENT}
                ),
            )
            self._cache[cache_key] = result

        return result[1]
