from datetime import timedelta

import pytest

from env_canada.ec_cache import Cache
from env_canada.ec_map import ECMap
from env_canada.ec_radar import ECRadar


@pytest.fixture(autouse=True)
def clear_cache_before_each():
    """Clear the cache before each test to ensure test isolation."""
    Cache.clear()
    yield
    Cache.clear()


class TestCacheClear:
    """Test Cache.clear() method"""

    def test_clear_all_entries(self):
        """Test clearing all cache entries when no prefix is provided."""
        # Add some entries
        Cache.add("key1", "value1", timedelta(hours=1))
        Cache.add("key2", "value2", timedelta(hours=1))
        Cache.add("key3", "value3", timedelta(hours=1))

        # Verify entries exist
        assert Cache.get("key1") == "value1"
        assert Cache.get("key2") == "value2"
        assert Cache.get("key3") == "value3"

        # Clear all
        count = Cache.clear()

        # Verify all entries are gone
        assert count == 3
        assert Cache.get("key1") is None
        assert Cache.get("key2") is None
        assert Cache.get("key3") is None

    def test_clear_with_prefix(self):
        """Test clearing only entries matching a prefix."""
        # Add entries with different prefixes
        Cache.add("location1-basemap", "basemap1", timedelta(hours=1))
        Cache.add("location1-layer-rain", "rain1", timedelta(hours=1))
        Cache.add("location1-legend", "legend1", timedelta(hours=1))
        Cache.add("location2-basemap", "basemap2", timedelta(hours=1))
        Cache.add("location2-layer-rain", "rain2", timedelta(hours=1))
        Cache.add("capabilities-rain", "caps", timedelta(hours=1))

        # Clear only location1 entries
        count = Cache.clear("location1")

        # Verify only location1 entries are gone
        assert count == 3
        assert Cache.get("location1-basemap") is None
        assert Cache.get("location1-layer-rain") is None
        assert Cache.get("location1-legend") is None
        # location2 and capabilities should still exist
        assert Cache.get("location2-basemap") == "basemap2"
        assert Cache.get("location2-layer-rain") == "rain2"
        assert Cache.get("capabilities-rain") == "caps"

    def test_clear_with_prefix_no_matches(self):
        """Test clearing with a prefix that doesn't match any entries."""
        Cache.add("key1", "value1", timedelta(hours=1))
        Cache.add("key2", "value2", timedelta(hours=1))

        count = Cache.clear("nonexistent")

        assert count == 0
        assert Cache.get("key1") == "value1"
        assert Cache.get("key2") == "value2"

    def test_clear_empty_cache(self):
        """Test clearing an already empty cache."""
        count = Cache.clear()
        assert count == 0

    def test_clear_empty_cache_with_prefix(self):
        """Test clearing with prefix on an empty cache."""
        count = Cache.clear("some-prefix")
        assert count == 0


class TestECMapClearCache:
    """Test ECMap.clear_cache() method"""

    def test_clear_cache_returns_count(self):
        """Test that clear_cache returns the number of cleared entries."""
        map_obj = ECMap(coordinates=(50, -100), layer="rain")

        # Add some cache entries that match this map's prefix
        prefix = map_obj._get_cache_prefix()
        Cache.add(f"{prefix}-basemap", "basemap", timedelta(hours=1))
        Cache.add(f"{prefix}-layer-rain-2025-01-01", "layer", timedelta(hours=1))
        Cache.add(f"{prefix}-legend-rain", "legend", timedelta(hours=1))
        Cache.add("capabilities-rain", "caps", timedelta(hours=1))

        # Clear the cache
        count = map_obj.clear_cache()

        # Should clear all 4 entries (3 with location prefix + 1 capabilities)
        assert count == 4
        assert Cache.get(f"{prefix}-basemap") is None
        assert Cache.get(f"{prefix}-layer-rain-2025-01-01") is None
        assert Cache.get(f"{prefix}-legend-rain") is None
        assert Cache.get("capabilities-rain") is None

    def test_clear_cache_does_not_affect_other_locations(self):
        """Test that clearing cache for one location doesn't affect others."""
        map1 = ECMap(coordinates=(50, -100), layer="rain")
        map2 = ECMap(coordinates=(45, -75), layer="rain")

        # Add cache entries for both maps
        prefix1 = map1._get_cache_prefix()
        prefix2 = map2._get_cache_prefix()

        Cache.add(f"{prefix1}-basemap", "basemap1", timedelta(hours=1))
        Cache.add(f"{prefix2}-basemap", "basemap2", timedelta(hours=1))

        # Clear only map1's cache
        map1.clear_cache()

        # map1's entries should be gone
        assert Cache.get(f"{prefix1}-basemap") is None
        # map2's entries should still exist
        assert Cache.get(f"{prefix2}-basemap") == "basemap2"

    def test_clear_cache_clears_capabilities(self):
        """Test that clear_cache also clears the capabilities cache for the layer."""
        map_obj = ECMap(coordinates=(50, -100), layer="rain")

        # Add capabilities cache
        Cache.add("capabilities-rain", "rain_caps", timedelta(hours=1))
        Cache.add("capabilities-snow", "snow_caps", timedelta(hours=1))

        # Clear the cache
        map_obj.clear_cache()

        # rain capabilities should be gone (matches the map's layer)
        assert Cache.get("capabilities-rain") is None
        # snow capabilities should still exist
        assert Cache.get("capabilities-snow") == "snow_caps"


class TestECRadarClearCache:
    """Test ECRadar.clear_cache() method"""

    def test_clear_cache_delegates_to_map(self):
        """Test that ECRadar.clear_cache() delegates to the underlying map."""
        radar = ECRadar(coordinates=(50, -100), precip_type="rain")

        # Add cache entries
        prefix = radar._map._get_cache_prefix()
        Cache.add(f"{prefix}-basemap", "basemap", timedelta(hours=1))
        Cache.add(f"{prefix}-layer-rain-2025-01-01", "layer", timedelta(hours=1))
        Cache.add("capabilities-rain", "caps", timedelta(hours=1))

        # Clear the cache
        count = radar.clear_cache()

        # Should clear all entries
        assert count == 3
        assert Cache.get(f"{prefix}-basemap") is None
        assert Cache.get(f"{prefix}-layer-rain-2025-01-01") is None
        assert Cache.get("capabilities-rain") is None

    def test_clear_cache_after_precip_type_change(self):
        """Test the use case of clearing cache after changing precip_type."""
        radar = ECRadar(coordinates=(50, -100), precip_type="rain")

        # Add cache entries for rain
        prefix = radar._map._get_cache_prefix()
        Cache.add(f"{prefix}-layer-rain-2025-01-01", "rain_layer", timedelta(hours=1))
        Cache.add("capabilities-rain", "rain_caps", timedelta(hours=1))

        # Change precip type and clear cache
        radar.precip_type = "snow"
        radar.clear_cache()

        # Cache entries for rain should be gone
        assert Cache.get(f"{prefix}-layer-rain-2025-01-01") is None
        # Capabilities for snow (the new layer) should be cleared
        assert Cache.get("capabilities-snow") is None
        # Note: capabilities-rain was already cleared by clear_cache()
        # since it clears capabilities for the current layer (snow)
