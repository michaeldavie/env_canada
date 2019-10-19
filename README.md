# Environment Canada (env_canada)

This package provides access meteorological data published by [Environment Canada](https://weather.gc.ca/index_e.html).

## Weather

`ECData` provides current conditions and forecast. It automatically determines which weather station to use based on latitude/longitude provided. It is also possible to specify a specific station code of the form `AB/s0000123` based on those listed in [this CSV file](http://dd.weatheroffice.ec.gc.ca/citypage_weather/docs/site_list_towns_en.csv). For example:

```
from env_canada import ECData

ec_en = ECData(coordinates=(lat, long))
ec_fr = ECData(station_id='ON/s0000430', language='french')

# current conditions
ec_en.conditions

# daily forecasts
ec_en.daily_forecasts

# hourly forecasts
ec_en.hourly_forecasts

# Update 
ec_en.update()
```

## Radar

`ECRadar` provides Environment Canada meteorological [radar imagery](https://weather.gc.ca/radar/index_e.html).

```
from env_canada import ECRadar

radar_coords = ECRadar(coordinates=(lat, long))
radar_station = ECRadar(station_id='XFT')

# Conditions Available
radar_coords.get_loop()
radar_station.get_latest_frame()
```

## Air Quality Health Index

`AqhiData` provides [Air Quality Health Index](http://www.airqualityontario.com/science/aqhi_description.php) current conditions and forecast . It automatically determines which region and zone to use based on latitude/longitude provided. It is also possible to specify a specific `region_cgndb` and `zone_abreviation` based on those listed in [this XML file](https://dd.weather.gc.ca/air_quality/doc/AQHI_XML_File_List.xml). For example:


```
from env_canada import AqhiData

aqhi_en = AqhiData(coordinates=(lat, long))
aqhi_fr = AqhiData(region_cgndb='AADCE', zone_abreviation='atl', language='french')

# current
aqhi_en.conditions
aqhi_fr.conditions

# daily forecasts
aqhi_fr.daily_forecasts

# hourly forecasts
aqhi_en.hourly_forecasts

# Update
aqhi_fr.update()
```

# License

The code is available under terms of [MIT License](LICENSE.md)