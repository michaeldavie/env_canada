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

    @classmethod
    def clear(cls, prefix: str | None = None) -> int:
        """Clear cache entries.

        Args:
            prefix: If provided, only clear entries whose keys start with this prefix.
                    If None, clear all entries.

        Returns:
            Number of entries cleared.
        """
        if prefix is None:
            count = len(cls._cache)
            cls._cache.clear()
            return count

        keys_to_delete = [key for key in cls._cache if key.startswith(prefix)]
        for key in keys_to_delete:
            del cls._cache[key]
        return len(keys_to_delete)
