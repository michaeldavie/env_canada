from aiohttp import ClientSession
from datetime import datetime, timedelta

CACHE_EXPIRE_TIME = timedelta(minutes=120)


class CacheClientSession(ClientSession):
    _cache = {}

    def __init__(self, raise_for_status=False):
        super().__init__(raise_for_status=raise_for_status)

    def _flush_cache(self):
        expiry_time = datetime.now()
        to_delete = [k for k,v in self._cache.items() if v[0] < expiry_time]

        for k in to_delete:
            print(f"_flush_cache expiring {self._cache[k][0]} {k}")
            del self._cache[k]

    async def get(self, url, params, cache_time=CACHE_EXPIRE_TIME):
        cache_key = (url, tuple(sorted(params.items())))
        result = self._cache.get(cache_key)

        if not result:
            print(f"cached get {'' if result else 'NOT '}found!   {cache_key}")
            result = (datetime.now() + cache_time, await super().get(url=url, params=params))
            self._cache[cache_key] = result

        self._flush_cache()
        return result[1]
