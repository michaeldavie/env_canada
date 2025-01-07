from datetime import datetime
from typing import Any, ClassVar


class Cache:
    _cache: ClassVar[dict[str, tuple[datetime, Any]]] = {}

    @classmethod
    def add(cls, cache_key, item, cache_time):
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
