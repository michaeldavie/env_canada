# Environment Canada (env_canada)

This package provides access meteorological data published by [Environment Canada](https://weather.gc.ca/index_e.html).

## Weather

`ECWeather` provides current conditions and forecast. It automatically determines which weather station to use based on latitude/longitude provided. It is also possible to specify a specific station code of the form `AB/s0000123` based on those listed in [this CSV file](http://dd.weatheroffice.ec.gc.ca/citypage_weather/docs/site_list_towns_en.csv). For example:

```
from env_canada import ECWeather

ec_en = ECWeather(coordinates=(lat, long))
ec_fr = ECWeather(station_id='ON/s0000430', language='french')

# current conditions
ec_en.conditions

# daily forecasts
ec_en.daily_forecasts

# hourly forecasts
ec_en.hourly_forecasts

# alerts
ec_en.alerts

# AQHI air quality
ec_en.aqhi

# Update 
ec_en.update()
```

## Radar

`ECRadar` provides Environment Canada meteorological [radar imagery](https://weather.gc.ca/radar/index_e.html).

```
from env_canada import ECRadar

radar_coords = ECRadar(coordinates=(lat, lon))

# Conditions Available
radar_coords.get_loop()
radar_station.get_latest_frame()
```

# License

The code is available under terms of [MIT License](LICENSE.md)
