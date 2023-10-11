# Changelog for `env_canada`

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
