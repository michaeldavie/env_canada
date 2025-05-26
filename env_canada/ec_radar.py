from datetime import date

from .ec_map import ECMap

__all__ = ["ECRadar"]


class ECRadar:
    def __init__(self, **kwargs):
        """Initialize the radar object."""

        # Extract ECRadar-specific parameters
        precip_type = kwargs.pop("precip_type", None)
        radar_opacity = kwargs.pop("radar_opacity", 65)

        # Rename radar_opacity to layer_opacity for ECMap
        kwargs["layer_opacity"] = radar_opacity

        # Set up precip type logic
        self._precip_type_setting = precip_type
        self._precip_type_actual = self.precip_type[1]

        # Map the actual precipitation type to the layer
        kwargs["layer"] = self._precip_type_actual

        # Create the underlying ECMap instance
        self._map = ECMap(**kwargs)

        # Expose common properties for backward compatibility
        self.language = self._map.language
        self.metadata = self._map.metadata
        self.image = self._map.image
        self.width = self._map.width
        self.height = self._map.height
        self.bbox = self._map.bbox
        self.map_params = self._map.map_params
        self.show_legend = self._map.show_legend
        self.show_timestamp = self._map.show_timestamp
        self.timestamp = getattr(self._map, "timestamp", None)

    @property
    def precip_type(self):
        """Get precipitation type as (setting, actual) tuple for backward compatibility."""
        if self._precip_type_setting in ["rain", "snow"]:
            return (self._precip_type_setting, self._precip_type_setting)
        self._precip_type_actual = (
            "rain" if date.today().month in range(4, 11) else "snow"
        )
        return ("auto", self._precip_type_actual)

    @precip_type.setter
    def precip_type(self, user_input):
        """Set precipitation type."""
        if user_input not in ["rain", "snow", "auto"]:
            raise ValueError("precip_type must be 'rain', 'snow', or 'auto'")
        self._precip_type_setting = user_input
        self._precip_type_actual = self.precip_type[1]
        # Update the underlying map layer
        self._map.layer = self._precip_type_actual

    @property
    def radar_opacity(self):
        """Get radar opacity for backward compatibility."""
        return self._map.layer_opacity

    @radar_opacity.setter
    def radar_opacity(self, value):
        """Set radar opacity for backward compatibility."""
        self._map.layer_opacity = value

    async def get_latest_frame(self):
        """Get the latest radar image from Environment Canada."""
        return await self._map.get_latest_frame()

    async def update(self):
        """Update the radar image."""
        await self._map.update()
        self.image = self._map.image

    async def get_loop(self, fps=5):
        """Build an animated GIF of recent radar images."""
        return await self._map.get_loop(fps)

    # Expose internal methods for backward compatibility if needed
    async def _get_dimensions(self):
        """Get time range of available radar images."""
        return await self._map._get_dimensions()

    async def _get_basemap(self):
        """Fetch the background map image."""
        return await self._map._get_basemap()

    async def _get_legend(self):
        """Fetch legend image."""
        return await self._map._get_legend()
