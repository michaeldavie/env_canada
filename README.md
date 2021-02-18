# Environment Canada (env_canada)

This package provides access to various data sources published by [Environment and Climate Change Canada](https://www.canada.ca/en/environment-climate-change.html).

## Weather Observations and Forecasts

`ECWeather` provides current conditions and forecasts. It automatically determines which weather station to use based on latitude/longitude provided. It is also possible to specify a specific station code of the form `AB/s0000123` based on those listed in [this CSV file](https://dd.weather.gc.ca/citypage_weather/docs/site_list_towns_en.csv). For example:

```python
import asyncio

from env_canada import ECWeather

ec_en = ECWeather(coordinates=(50, -100))
ec_fr = ECWeather(station_id='ON/s0000430', language='french')

asyncio.run(ec_en.update())

# current conditions
ec_en.conditions

# daily forecasts
ec_en.daily_forecasts

# hourly forecasts
ec_en.hourly_forecasts

# alerts
ec_en.alerts
```

## Weather Radar

`ECRadar` provides Environment Canada meteorological [radar imagery](https://weather.gc.ca/radar/index_e.html).

```python
import asyncio

from env_canada import ECRadar

radar_coords = ECRadar(coordinates=(50, -100))

# Conditions Available
animated_gif = asyncio.run(radar_coords.get_loop())
latest_png = asyncio.run(radar_coords.get_latest_frame())
```

## Air Quality Health Index (AQHI)

`ECAirQuality` provides Environment Canada [air quality](https://weather.gc.ca/airquality/pages/index_e.html) data.

```python
import asyncio

from env_canada import ECAirQuality

aqhi_coords = ECAirQuality(coordinates=(50, -100))

asyncio.run(aqhi_coords.update())

# Data available
aqhi_coords.current
aqhi_coords.forecasts
```

## Water Level and Flow

`ECHydro` provides Environment Canada [hydrometric](https://wateroffice.ec.gc.ca/mainmenu/real_time_data_index_e.html) data.

```python
import asyncio

from env_canada import ECHydro

hydro_coords = ECHydro(coordinates=(50, -100))

asyncio.run(hydro_coords.update())

# Data available
hydro_coords.measurements
```

# License

The code is available under terms of [MIT License](LICENSE.md)
