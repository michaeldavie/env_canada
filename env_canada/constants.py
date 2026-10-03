from aiohttp import ClientTimeout

USER_AGENT = "env_canada/0.20.5"

# Every request uses this except the basemap, which has its own
# (ec_map.BASEMAP_TIMEOUT). Environment Canada's services answer in well
# under a second once connected, so the whole request is bounded at 10 s to
# stop a hung one holding update() for aiohttp's five-minute default.
#
# The connect has a bound of its own, much shorter than the whole request. A
# new connection sometimes hangs for about twenty seconds before it is
# accepted (seen from GitHub's runners; healthy ones take 30 ms), and a
# retry (ec_http.get_with_retry) fixes that, but only if the stalled attempt
# is given up on early enough to leave time for it.
CLIENT_TIMEOUT = ClientTimeout(total=10, sock_connect=5)
