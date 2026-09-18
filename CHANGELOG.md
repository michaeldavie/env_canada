# Changelog for `env_canada`

## Unreleased

### Changes

- CI now tests on Python 3.11 through 3.14, rather than only whichever interpreter the runner happens to ship, so the declared floor and the version Home Assistant runs are both covered. The workflow also runs with a read-only token, cancels superseded runs, and no longer runs twice for a pull request opened from a branch in this repository
- Ruff and mypy now run from the project's locked environment everywhere - pre-commit, CI and a plain `uv run` - so all three use the same version and the same configuration. Previously pre-commit ran its own mypy with `--ignore-missing-imports` and without the lxml and pandas stubs, and passed, while `uv run mypy env_canada` failed on geopy. mypy is now configured in `pyproject.toml` with a geopy override
- Report branch coverage in CI via pytest-cov, and enable ruff's import sorting
- Remove three tests that could never run - two placeholders that skipped unconditionally, and a radar snapshot test disabled when `ECRadar` was rebuilt on `ECMap` - along with the 34 fixture files only the latter used, and a duplicate layer-validation test
- Add `AGENTS.md`, a checked-in brief for AI coding agents and contributors: module map, commands, the Home Assistant API contract, testing rules and the release steps
- README: remove the Snyk badge, which no longer resolves, and correct the note claiming legend styles are discovered from the WMS server; legends have been rendered locally since v0.15.0

## v0.20.3

### Changes

- Fix a GetCapabilities response that isn't usable XML - a proxy error page, a truncated body - raising `lxml.etree.XMLSyntaxError` out of `update()` and taking down everything that call was building. It now degrades to "no image" and is left out of the cache, so a recovered server is picked up on the next poll rather than after the cache expires. Affects `ECMap`, `ECRadar` and `ECPrecipForecast`
- **ECMap**: Fix the radar loop asking for a timestamp the server has already dropped. GeoMet slides a fixed-width window forward, retiring the oldest step as it publishes a new one, so a GetCapabilities response held in the cache across a publication names a `start` that is no longer served - which is what produces the `code="NoMatch"` exception behind the broken radar images in [#160](https://github.com/michaeldavie/env_canada/issues/160). The loop's oldest frame now moves forward with the window, counting the grid instants that have passed since the response was read ([#160](https://github.com/michaeldavie/env_canada/issues/160))
- GetCapabilities responses are now cached for the cadence the layer's own time dimension advertises, bounded to between 1 and 15 minutes, rather than a fixed 5 minutes. A layer is worth re-reading about once per publication: 5 minutes was arbitrary against the radar layers' `PT6M`, and wasteful against HRDPS's `PT1H`
- Correct the description of the v0.20.0 radar fix, which attributed it to GeoMet having no data for timestamps inside its own advertised range. Probing every advertised step of both radar layers found no such gaps; the timestamps it declines are ones outside what the layer holds, which is what the fixes in v0.19.2, v0.20.1 and this release each address a cause of

## v0.20.2

### Changes

- **ECMap**: Fix a single frame failing with an HTTP error status or a timeout aborting the whole loop. `_get_layer_image()` caught only `ClientConnectorError`, but `ClientSession(raise_for_status=True)` raises `ClientResponseError` for a 5xx and aiohttp raises `TimeoutError` on a timeout, so one flaky frame out of the ~31 in a default loop took the entire radar image down rather than being skipped like any other missing frame ([#160](https://github.com/michaeldavie/env_canada/issues/160))
- **ECMap**: Fix a skipped frame staying blank for hours after the server recovered. A frame rendered without its radar layer was cached for the same 200 minutes as a good one, and since a frame ages out of the server's ~3-hour window at about the same time, a frame that failed once was effectively blank for its whole life in the loop. Those frames are now cached for 2 minutes, so the next poll retries them; successfully rendered frames are unaffected ([#160](https://github.com/michaeldavie/env_canada/issues/160))
- **ECMap**: Fix a truncated image - a connection dropped mid-response - being cached as a valid frame and then raising `OSError` while assembling the loop. Frame validation used `Image.open()`, which only reads the header; it now decodes the pixel data as well
- **ECMap**: Log the layer and timestamp when a frame can't be retrieved, rather than just "Layer could not be retrieved"

## v0.20.1

### Changes

- **ECMap**: Fix `loop_minutes`/`future_minutes` values that aren't a multiple of the layer's advertised time step (e.g. 65 minutes on a 6-minute grid) shifting every frame in the loop off-grid, causing GeoMet to reject the whole loop's worth of requests as "time outside valid hours" instead of just the one frame the previous fix anticipated. The loop's start/end are now snapped to whole multiples of the grid step, anchored on "now" (which is always grid-aligned)

## v0.20.0

### Changes

- **ECPrecipForecast**: New class assembling the numeric precipitation series needed to draw a precipitation histogram. Produces a 6-minute precipitation rate series (observed radar plus radar extrapolation, roughly the next hour) and an hourly amount/probability/type series (HRDPS and its WEonG diagnostics, up to 48 hours), by querying Environment Canada's WMS server with `GetFeatureInfo` point queries. Hourly amounts are differenced from the model's run-cumulative accumulation field, with every request pinned to a single model run
- Extract the shared GeoMet WMS plumbing — the HTTP call, the bounding-box maths, and the GetCapabilities dimension parsing — into `ec_geomet`, so `ECMap` and `ECPrecipForecast` share one implementation. Dimension parsing now also reports the advertised time step, which the WMS server requires requests to land on exactly
- **Coordinate validation**: Fix `coordinates` being accepted when out of range or given the wrong way round. Voluptuous reads a tuple schema as "every element matches any one of these validators" rather than as positional, so the previous `(Range(-90, 90), Range(-180, 180))` schema accepted a latitude of 95 — it matched the longitude validator — and silently queried the wrong location. Affected `ECAirQuality`, `ECHydro`, `ECMap`, `ECRadar` and `ECWeather`; all now share a validator in `ec_validate`
- **ECRadar**: Fix `_get_legend()` raising `AttributeError`. It forwarded to `ECMap._get_legend()`, which does not exist; the method it wants is `_generate_legend()`
- **ECMap**: Use the frame interval the layer's time dimension advertises rather than assuming 6 minutes. No change for the radar layers, which are all `PT6M`
- **ECMap**: Fix a crash (`PIL.UnidentifiedImageError`) when GeoMet declines to serve a requested radar timestamp. It answers with an `ogc:ServiceExceptionReport` XML body under an HTTP 200 status rather than an error status, so the XML was cached and returned as if it were frame data, taking the whole radar image down over a single frame - and, because the bad response stayed cached, keeping it down long after the server recovered. Frame data is now validated before being cached, and an unusable frame is skipped (this entry was omitted when v0.20.0 was released; the fix is in that version) ([#160](https://github.com/michaeldavie/env_canada/issues/160))
- Fix `_text_size()` in `ec_legend` being annotated as returning `tuple[int, int]` while returning floats
- Fix two `--run-slow` tests that could not pass: one asserted against a snapshot that had never been recorded, the other requested a radar frame from a hardcoded February 2025 timestamp, long outside the few hours of frames the server retains. Neither runs in CI, so both had gone unnoticed

## v0.19.2

### Changes

- **ECMap**: Fix a crash (`PIL.UnidentifiedImageError`) when `future_minutes` is set and a frame's time falls after the observed layer's last frame but before the extrapolation (nowcast) layer's own data window opens. The two layers refresh on independent schedules, so this gap can appear depending on where the nowcast model's run cycle happens to be; affected frames are now clamped to the extrapolation layer's earliest available time instead of requesting an invalid time from the observed layer

## v0.19.1

### Changes

- Fix broken "Python Lint and Test" badge URL in README
- Switch PyPI publishing to trusted publishing (OIDC) with PEP 740 attestations, replacing username/password secrets

## v0.19.0

### Changes

- **ECMap / ECRadar**: Add `future_minutes` init parameter to extend `get_loop()` past "now" using Environment Canada's radar extrapolation (nowcast) WMS layers, pairing backward-looking observed frames with forward-looking forecast frames in a single animation. Only has an effect for the `rain`/`snow` layers (`precip_type` has no extrapolation layer). Defaults to `0`, matching previous behaviour

## v0.18.0

### Changes

- **ECMap / ECRadar**: Add `interpolation` init parameter to smooth the WMS-rendered radar layer instead of leaving it pixelated, using the `INTERPOLATION=TRUE` WMS parameter (suggested in [#143](https://github.com/michaeldavie/env_canada/issues/143)). Defaults to `False`, matching previous behaviour
- **ECMap / ECRadar**: Add `webp` init parameter to request the radar layer from Environment Canada's WMS as WebP instead of PNG, and return `get_latest_frame()`/`get_loop()` output as WebP instead of PNG/GIF, trading lower bandwidth for higher per-frame latency. Defaults to `False`, matching previous behaviour

## v0.17.0

### Changes

- **ECMap / ECRadar**: Add `colors` init parameter to select between the 8 and 14 colour radar scales published by Environment Canada's WMS (fixes [#143](https://github.com/michaeldavie/env_canada/issues/143)). Defaults to `14`, matching previous behaviour

## v0.16.1

### Changes

- **ECMap**: Fix `math domain error` and unclamped latitude in the bounding box calculation when a requested map circle encloses a pole (fixes [#141](https://github.com/michaeldavie/env_canada/issues/141))

## v0.16.0

### Changes

- **ECMap**: Add `fps` and `loop_minutes` init parameters to control the radar loop's frame rate and how far back the animation goes (fixes [#115](https://github.com/michaeldavie/env_canada/issues/115)). Both default to the previous behaviour (`fps=5`, full available range)

## v0.15.0

### Changes

- **ECMap / ECRadar**: Replace WMS-fetched legend with locally generated horizontal legends for all three radar layers (rain, snow, precip_type), matching the Environment Canada weather map style
- **ECMap / ECRadar**: Bundle DejaVu Sans font for legend labels and timestamp overlay, replacing the old bitmap font
- **ECMap**: Composite cache key now includes language to correctly separate EN/FR cached frames

## v0.14.1

### Changes

- **ECHistorical**: Add support for alphanumeric climate IDs (#132)

## v0.14.0

### Changes

- **ECHistorical**: Switch from station ID to climate ID for historical weather data queries

## v0.13.2

### Bug Fixes

- **ECAlerts**: Fix over-reporting of alerts by replacing the bounding-box-only approach with a 50 km BBOX pre-filter combined with client-side point-in-polygon filtering, so only alerts whose coverage polygon actually contains the queried coordinates are returned

## v0.13.1

### Bug Fixes

- **ECWeather**: Fix `TypeError` crash when forecast period or text summary is `None` (#130)

## v0.13.0

### New Features

- **ECAlerts**: New class providing weather alerts from the GeoMet WFS `Current-Alerts` layer with richer data than the XML source: full alert text, affected area, risk colour, confidence, and impact
- **ECWeather**: `update()` now uses `ECAlerts` internally; falls back to XML alerts on failure. New `alert_features` property exposes raw WFS feature properties

## v0.12.4

### New Features

- **Cache**: Add `Cache.clear()` class method to clear cache entries by prefix or all entries
- **ECMap**: Add `clear_cache()` method to clear cached basemap, layer, legend, and capabilities data for a map instance
- **ECRadar**: Add `clear_cache()` method to clear cached radar data, useful when changing `precip_type` to ensure fresh images are fetched

## v0.12.3

### Bug Fixes

- **ECWeather**: Fix alert endings not being parsed from Environment Canada data
  - Changed `ALERT_TYPE_TO_NAME` mapping from `"ending"` to `"ended"` to match actual XML attribute values
  - Fixes issue where "Endings" sensor always showed 0 despite ended alerts being present in the data

## v0.12.2

### New Features

- **ECWeather**: Add support for parsing hourly UV index from Environment Canada data
- **ECWeather**: Add additional alert variables including alert_level, alert_type, and alert_region for enhanced weather alert information

### Documentation

- **README**: Update documentation and examples

## v0.12.1

### Bug Fixes

- **Packaging**: Fix font file inclusion in wheel distribution
  - Replace package-dir with explicit package-data declaration
  - Ensures 10x20.pil and 10x20.pbm files are included in installed package
  - Fixes FileNotFoundError when rendering radar timestamps

## v0.12.0

### New Features

- **get_ec_sites_list**: Add new async function for UI dropdown selection
  - Returns formatted list of weather stations with city/province labels
  - Provides 3-digit station codes as values for easy integration
  - Designed for use in Home Assistant config flows and other UIs

## v0.11.3

### Bug Fixes

- **API URLs**: Update Environment Canada API URLs to use new 'today' endpoint structure
  - ECWeather: Updated SITE_LIST_URL to `site_list_towns_en.csv`
  - ECHydro: Updated URLs to use 'today' path prefix
  - ECAQHI: Updated URLs to use 'today' path prefix
  - Fixes 404 errors from outdated URL endpoints
- **ECHydro**: Fix test to handle optional discharge measurements

### Infrastructure

- Remove unused dependencies: imageio and numpy

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
