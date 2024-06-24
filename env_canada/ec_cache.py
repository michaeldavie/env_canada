from datetime import datetime, timedelta

CACHE_EXPIRE_TIME = timedelta(minutes=200)  # Time is tuned for 3h radar image


class Cache:
    _cache = {}

    @classmethod
    def add(cls, cache_key, item, cache_time=CACHE_EXPIRE_TIME):
        cls._cache[cache_key] = (datetime.now() + cache_time, item)
        return item  # Returning item useful for chaining calls

    @classmethod
    def get(cls, cache_key):
        # Flush at start so we don't use expired entries
        now = datetime.now()
        expired = [key for key, value in cls._cache.items() if value[0] < now]
        for key in expired:
            # print(f"Flushing: {key}")
            del cls._cache[key]

        result = cls._cache.get(cache_key)
        return result[1] if result else None
