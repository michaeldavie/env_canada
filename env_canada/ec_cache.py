from datetime import datetime, timedelta

CACHE_EXPIRE_TIME = timedelta(minutes=200)  # Time is tuned for 3h radar image


class Cache:
    _cache = {}

    @classmethod
    def flush(cls):
        """Empty the cache."""
        _cache = {}  # type: ignore

    @classmethod
    def add(cls, cache_key, item, cache_time=CACHE_EXPIRE_TIME):
        """Add an entry to the cache."""

        cls._cache[cache_key] = (datetime.now() + cache_time, item)
        return item  # Returning item useful for chaining calls

    @classmethod
    def get(cls, cache_key):
        """Get an entry from the cache."""

        # Delete expired entries at start so we don't use expired entries
        now = datetime.now()
        expired = [key for key, value in cls._cache.items() if value[0] < now]
        for key in expired:
            del cls._cache[key]

        result = cls._cache.get(cache_key)
        return result[1] if result else None
