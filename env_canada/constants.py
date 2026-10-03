from aiohttp import ClientTimeout

USER_AGENT = "env_canada/0.20.5"

# Every request uses this except the basemap, which has its own
# (ec_map.BASEMAP_TIMEOUT). Environment Canada's services answer in well
# under a second once connected, so the whole request is bounded at 10 s to
# stop a hung one holding update() for aiohttp's five-minute default.
#
# The connect has a bound of its own, much shorter than the whole request. A
# new connection sometimes sits in the connect for seconds, or over a minute,
# before it is accepted (seen from GitHub's runners; a healthy one takes 30
# ms, and none has been seen from a home connection). The cause is unknown.
# Giving up on a stalled attempt early lets ec_http.get_with_retry try again,
# which helps only partly: the stalls come in runs, and worsen when many
# connections are open at once. Fewer connections is the remedy.
CLIENT_TIMEOUT = ClientTimeout(total=10, sock_connect=5)
