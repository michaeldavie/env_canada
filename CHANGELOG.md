Changelog for env_canada API

## [v0.5.14]
- Change update in radar to save image
- Always get site data for weather so that lat/lon/station can be fully validated

## [v0.5.13]
- Change hydrometric URL for retrieving data
- Change weather data API back to "slow" servers -- fast servers not reliable

## [v0.5.12]
- Add attribution infomation available to radar and AQHI API
- Add french label in radar for snow/rain

## [v0.5.11]
- Add attribution infomation available to weather API

## [v0.5.10]
- Add normal_high and normal_low sensor values

## [v0.5.9]
- Save region and timestamp in AQHI for API users to retrieve

## [v0.5.8]
- Add error checking on bad XML when get weather

## [v0.5.7]
- Fix init issue on AQHI
- Add radar `update` for HA (does a get_loop)

## [v0.5.6]
- Improve auto snow/rain checking on radar

## [v0.5.5]
- Improve radar voluptuous checking

## [v0.5.4]
- Bug fix radar voluptuous

## [v0.5.3]
- Check AQHI zone
- Allow `precip_type` of `None` meaning `auto`

## [v0.5.2]
- Add voluptuous checking on all __init__ parameters
- Add `raise_for_status=True` on aiohttp ClientSessions

## [v0.5.1]
- Switch to "high speed" server for retrieving weather data

## [v0.5.0]
- Add ability to retrieve historical data

## [v0.4.1]
- - Internal refactor of code for show timestamp and show legend

## [v0.4.0]
- Added type info for weather XML data and use typing when creating return value
- Switched from unparsed datetime to datetime object in output

## [v0.3.2]
- Added ability to set frames per second for radar image

## [v0.3.1]
- Removed ability to specify station for radar (only lat/lon supported)
- Tweak to radar image adding opacity

## [v0.3.0]
- Switch to ECWeather class from ECData
- Split off AQHI retrieval into separate class
- Switch to asyncio from blocking IO