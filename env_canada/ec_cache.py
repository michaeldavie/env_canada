from aiohttp import ClientSession
from datetime import datetime, timedelta

CACHE_EXPIRE_TIME = timedelta(minutes=200)


class CacheClientSession(ClientSession):
    """Shim to cache ClientSession requests."""

    _cache = {}

    def _flush_cache(self):
        """Flush expired cache entries."""
        expiry_time = datetime.now()
        to_delete = [k for k,v in self._cache.items() if v[0] < expiry_time]

        for k in to_delete:
            print(f"_flush_cache expiring {self._cache[k][0]} {k}")
            del self._cache[k]

    async def get(self, url, params, cache_time=CACHE_EXPIRE_TIME):
        """Thin wrapper around ClientSession.get to cache responses."""
        self._flush_cache()

        cache_key = (url, tuple(sorted(params.items())))
        result = self._cache.get(cache_key)

        if not result:
            result = (datetime.now() + cache_time, await super().get(url=url, params=params))
            self._cache[cache_key] = result
            print(f"cached get NOT found!  {cache_key}")

        return result[1]
