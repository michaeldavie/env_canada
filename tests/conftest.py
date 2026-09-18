import pytest

from env_canada.ec_cache import Cache


@pytest.fixture(autouse=True)
def clear_cache():
    """`Cache` is a process-wide dict, so whatever one test leaves in it is
    visible to the next - a test that primed it with a stand-in response
    could make an unrelated one fail depending on the order they ran in.
    Start and finish every test with it empty."""
    Cache.clear()
    yield
    Cache.clear()


def pytest_addoption(parser):
    parser.addoption(
        "--run-slow",
        action="store_true",
        help="don't skip the tests marked with @pytest.mark.slow",
    )


def pytest_configure(config):
    # register an additional marker
    config.addinivalue_line(
        "markers",
        "slow: mark the test a slow (requires network or lots of compute)",
    )


def pytest_runtest_setup(item):
    if "slow" in item.keywords and not item.config.getoption("--run-slow"):
        pytest.skip("Use `pytest --run-slow` to run this test")
