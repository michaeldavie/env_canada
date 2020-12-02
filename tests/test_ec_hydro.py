import asyncio
from datetime import datetime

import pytest

from env_canada import ec_hydro, ECHydro


def test_get_hydro_sites():
    sites = asyncio.run(ec_hydro.get_hydro_sites())
    assert len(sites) > 0


@pytest.mark.parametrize(
    "init_parameters",
    [{"coordinates": (50, -100)}, {"province": "ON", "station": "02KF005"}],
)
def test_echydro(init_parameters):
    hydro = ECHydro(**init_parameters)
    assert isinstance(hydro, ECHydro)


@pytest.fixture()
def test_hydro():
    return ECHydro(coordinates=(50, -100))


def test_update(test_hydro):
    asyncio.run(test_hydro.update())
    assert test_hydro.measurements["water_level"]
