import pytest


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
