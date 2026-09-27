from aiohttp import ClientTimeout

USER_AGENT = "env_canada/0.20.4"

# Every request uses this except the basemap, which has its own
# (ec_map.BASEMAP_TIMEOUT). Environment Canada's services answer in well
# under a second - GeoMet's slowest of ~260 timed requests took 0.9 s - so
# this bounds a hung connection without cutting off a slow but healthy one.
CLIENT_TIMEOUT = ClientTimeout(total=10)
