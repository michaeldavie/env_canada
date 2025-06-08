# Changelog for `env_canada`

## Unreleased

## v0.11.2

### Bug Fixes

- **ECMap**: Fix multi-instance caching issue where different geographic locations shared cached data
  - Cache keys now include location-specific prefix to prevent data sharing between coordinates
  - Ensures each ECRadar/ECMap instance maintains separate caches for basemap, legend, and radar overlays

## v0.11.1

### Bug Fixes

- **ECWeather**: Fix Home Assistant compatibility issue with station ID handling
  - Keep `station_id` as string for external API compatibility
  - Move one-time initialization from `update()` to `_resolve_station()` method for better performance
  - Add `station_tuple` property for accessing internal tuple representation
  - Update `validate_station` to return input unchanged instead of extracting digits

## v0.11.0

### Major Features

- **New ECMap class**: Complete weather map functionality using Environment Canada's WMS layers
  - Support for rain, snow, and precipitation type radar layers
  - Dynamic legend discovery from WMS capabilities
  - Customizable map dimensions, radius, opacity, and overlay options
  - Animated GIF creation for weather loops
  - Full English/French language support

### New Features

- **Flexible station ID formats**: Support for multiple station ID input formats:
  - Full format: `"AB/s0000123"` (province code and full station ID)
  - Station ID only: `"s0000123"` (province resolved automatically)
  - Numeric only: `"123"` (just the station number)
- **Dynamic file discovery**: Automatic handling of Environment Canada's new timestamped weather file format (effective June 2025)
- **Enhanced ECRadar**: Now uses ECMap as internal implementation while maintaining full backward compatibility

### Improvements

- **Mapbox dependency removed**: Now exclusively uses Canadian government data sources (Natural Resources Canada + Environment Canada)
- **Proper logging**: Module-specific loggers throughout codebase
- **Enhanced error handling**: Robust network failure handling and caching
- **Station ID validation**: Improved validation with regex patterns and automatic province resolution
- **Type safety**: Full mypy compliance and enhanced type annotations

### Infrastructure

- Automatic adaptation to Environment Canada's infrastructure changes
- Enhanced test coverage with comprehensive mocking
- Support for dynamic URL structure discovery

## v0.10.0

- BREAKING CHANGE: AQHI `metadata` changed from `dict` type to a `dataclass` providing better type checking and discoverability

## v0.9.0

- BREAKING CHANGE: Weather update now only has `ECWeatherUpdateException` on network error
- BREAKING CHANGE: Weather `metadata` changed from `dict` type to a `dataclass` providing better type checking and discoverability
- On a caught exception cached data will be returned if the data is not stale (older than `max_age`, which defaults to 2 hours)
- When cached data is returned a cached data return count is incremented in `metadata` so that API users know the data returned is unchanged from the previous `update` call
- The cached data count is reset to 0 on any successful `update`
- A last error string is stored in `metadata` for any caught exception in update

## v0.8.0

- Change packaging to `pyproject.toml`
- Improve code checks
- Switch from `defusedxml` to `lxml`
- Update Github actions
- Fix historical range query

## v0.7.2

- Add timestamps to daily forecasts

## v0.7.1

- Fix memory leak and improve performance of `ec_radar`

## v0.7.0

- BREAKING CHANGE: Remove yesterday's data (high temp, low temp, and precipitation) from weather. Environment Canada removed these values from the source data.
- Make calls to `PIL` asynchronous

## v0.6.2

- Fix imports
- Call `imageio` in executor
- Remove test for weather with no conditions

## v0.6.1

- Dependency updates

## v0.6.0

- Support weather stations without current conditions
- Fix sunrise and sunset conditions
- Switch radar URL to HTTPS

## v0.5.37

- Update dependencies

## v0.5.36

- Handle non-numeric hourly wind speed
- Set `imageio` dependency >= 2.28.0

## v0.5.35

- Add wind speed and direction to hourly forecasts
- Handle missing AQHI forecasts
- Fixes for dependency changes

## v0.5.34

- Fix radar GIF for latest `Pillow`

## v0.5.33

- Fix handling unexpected strings

## v0.5.32

- Generalize handling unexpected strings

## v0.5.31

- Handle wind speed of "calm" km/h

## v0.5.30

- Pin `numpy` version
- Update XML encoding

## v0.5.29

- Handle forecast temperatures of 0º

## v0.5.28

- Raise error on old weather data

## v0.5.27

- Change radar frame interval

## v0.5.26

- Add sunrise and sunset to weather
- Ignore bad hydrometric site data

## v0.5.25

- Add support for hourly historical data
- Add `pandas` dependency

## v0.5.24

- Add yesterday's low and high temperature

## v0.5.23

- Use `defusedxml` for XML parsing

## v0.5.22

- Refresh radar legend on layer change
- Automatically update radar layer

## v0.5.21

- Handle missing AQHI observations
- Add logging

## v0.5.20

- Add Mapbox as fallback map source

## v0.5.19

- Work around GeoGratis map service outage

## v0.5.18

- Add user agent

## v0.5.17

- Add caching of web requests to radar

## v0.5.16

- Make radar `precip_type` stable

## v0.5.15

- Exclude `tests` from build

## v0.5.14

- Change update in radar to save image
- Always get site data for weather so that lat/lon/station can be fully validated

## v0.5.13

- Change hydrometric URL for retrieving data
- Change weather data API back to "slow" servers -- fast servers not reliable

## v0.5.12

- Add attribution infomation available to radar and AQHI API
- Add French label in radar for snow/rain

## v0.5.11

- Add attribution infomation available to weather API

## v0.5.10

- Add normal_high and normal_low sensor values

## v0.5.9

- Save region and timestamp in AQHI for API users to retrieve

## v0.5.8

- Add error checking on bad XML when fetching weather

## v0.5.7

- Fix init issue on AQHI
- Add radar `update` for HA (alias of `get_loop()`)

## v0.5.6

- Improve auto snow/rain checking on radar

## v0.5.5

- Make `precip_type` a property of radar objects

## v0.5.4

- Bug fix radar `voluptuous`

## v0.5.3

- Check AQHI zone
- Allow `precip_type` of `None` meaning `auto` for radar image

## v0.5.2

- Add `voluptuous` checking on all `__init__` parameters
- Add `raise_for_status=True` on `aiohttp.ClientSession()`

## v0.5.1

- Switch to "high speed" server for retrieving weather data

## v0.5.0

- Add ability to retrieve historical data

## v0.4.1

- Make radar timestamp and legend optional

## v0.4.0

- Add type info for weather XML data and use typing when creating return value
- Switch from unparsed datetime to `datetime` object in output

## v0.3.2

- Make radar GIF frames per second configurable
- Make radar opacity configurable

## v0.3.1

- Remove ability to specify station for radar (only lat/lon supported)

## v0.3.0

- Switch to ECWeather class from ECData
- Split off AQHI retrieval into separate class
- Switch to asyncio from blocking IO
- Add tests
