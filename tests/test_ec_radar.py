from env_canada import ec_radar


def test_get_station_coords():
    coords = ec_radar.get_station_coords("XFT")
    assert coords == (45.04101, -76.11617)
