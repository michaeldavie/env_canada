# AGENTS.md

Guidance for AI coding agents and for anyone contributing to this repository. Keep it accurate: it is the brief every agent session starts from.

## Project overview

`env_canada` is a Python 3.11+ library for data published by Environment and Climate Change Canada. Its main consumer is Home Assistant's `environment_canada` integration, which pins an exact version and imports `ECWeather`, `ECMap`, `ECAirQuality`, `ECWeatherUpdateFailed`, `ec_exc` and `ec_weather.get_ec_sites_list`. Everything Home Assistant reads is public API: attribute names, the shapes of the result dicts, and the exception types `ECWeatherUpdateFailed` and `UnknownStationId`. Change those only with a deprecation period.

One class per module:

- **ECWeather** (`ec_weather.py`): current conditions, daily/hourly forecasts and alerts from the citypage weather XML on dd.weather.gc.ca. Finds the latest file by scraping the hourly directory listing
- **ECAlerts** (`ec_alerts.py`): alerts from the GeoMet WFS `Current-Alerts` layer with point-in-polygon filtering; ECWeather uses it internally
- **ECMap** (`ec_map.py`): WMS radar imagery (`rain`, `snow`, `precip_type` layers) composited over a basemap with a locally rendered legend and timestamp; builds animated loops, optionally extended with the radar extrapolation layers
- **ECRadar** (`ec_radar.py`): backward-compatibility wrapper around ECMap. Home Assistant uses ECMap directly
- **ECPrecipForecast** (`ec_precip_forecast.py`): 6-minute and hourly precipitation series from WMS GetFeatureInfo point queries
- **ECAirQuality** (`ec_aqhi.py`): AQHI observations and forecasts (XML)
- **ECHydro** (`ec_hydro.py`): water level and discharge (CSV)
- **ECHistorical / ECHistoricalRange** (`ec_historical.py`): historical climate data scraped from climate.weather.gc.ca; the range class depends on pandas

Shared modules: `ec_geomet.py` (GeoMet WMS HTTP call, bounding-box maths, GetCapabilities dimension parsing), `ec_legend.py` (legend rendering; bundles `DejaVuSans.ttf`), `ec_validate.py` (coordinate validator), `ec_cache.py` (process-wide TTL cache), `ec_exc.py` (exceptions), `constants.py` (`USER_AGENT`, which carries the version).

## Commands

The project uses [uv](https://docs.astral.sh/uv/). Every tool runs from the locked environment, so versions match between a contributor's machine, pre-commit and CI.

- `uv sync` - install dependencies
- `uv run pytest` - fast tests; tests that need the network are skipped
- `uv run pytest --run-slow` - also run the tests that hit Environment Canada's live services. Run these before every release: they are the only end-to-end check
- `uv run pytest tests/ec_weather_test.py::test_name` - one test
- `uv run pytest --cov` - with branch coverage, as CI runs it
- `uv run ruff check`, `uv run ruff format`, `uv run mypy` - lint, format and type-check. pre-commit runs exactly these commands
- `uv run pylint env_canada` - optional; it has no configuration and is noisy

## Architecture notes

All data classes follow one pattern: construct with coordinates or a station/region id, `await update()`, then read attributes. Coordinates resolve to the nearest station with geopy. HTTP uses aiohttp with a per-request `ClientSession` and `USER_AGENT`. Responses are XML (lxml), CSV or JSON.

GeoMet behaviours the tests encode and that must not regress:

- A time sent to the WMS must land exactly on the dimension's advertised step. A time outside the layer's window returns an XML ServiceExceptionReport under HTTP 200, so it has to be detected from the body
- GetCapabilities responses are cached. The radar window slides, so `LayerDimension.effective_start` moves the loop's start forward as time passes
- A single failed frame is skipped; it never aborts a loop

## Testing rules

- Every new test uses a fixture captured from the real service (`tests/fixtures/`) or a clearly labelled synthetic response modelled on one. No test asserts only on mock call counts
- No unconditional skips. A test that cannot run is deleted
- Name tests for the behaviour they protect and put the "why" in the docstring. `tests/ec_geomet_test.py` is the house style
- Network-dependent tests are marked `@pytest.mark.slow`
- `Cache` is process-wide; `conftest.py` clears it around every test, so individual tests do not need to
- Snapshots (syrupy) live in `tests/__snapshots__/`

## Committing

1. `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`
1. Stage specific files and review `git diff --staged`
1. Add a line under `## Unreleased` in `CHANGELOG.md` in the same commit
1. If pre-commit modifies files, add them and amend

## Releasing (maintainer)

The version is duplicated in `pyproject.toml` (`version`), `env_canada/constants.py` (`USER_AGENT`) and `CHANGELOG.md`. Nothing checks that they match the tag, so check by hand.

1. Update all three, commit as `Bump version to vX.Y.Z`, push main
1. `git tag vX.Y.Z && git push origin vX.Y.Z`
1. `gh release create vX.Y.Z --title "vX.Y.Z" --notes "..."`. The release event triggers `python-publish.yml` (uv build, PEP 740 attestations, trusted publishing)
1. Open the Home Assistant PR that bumps `env-canada==X.Y.Z` in the integration manifest
