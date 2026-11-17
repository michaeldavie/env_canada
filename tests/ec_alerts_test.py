import json
from unittest.mock import AsyncMock, patch

import pytest

from env_canada import ECAlerts
from env_canada.ec_cache import Cache


@pytest.fixture(autouse=True)
def clear_alerts_cache():
    """Clear the alerts cache before each test to prevent cross-contamination."""
    Cache.clear(prefix="alerts-")
    yield
    Cache.clear(prefix="alerts-")


def test_ecalerts_init():
    alerts = ECAlerts(coordinates=(50, -100))
    assert isinstance(alerts, ECAlerts)
    assert alerts.lat == 50
    assert alerts.lon == -100
    assert alerts.language == "english"
    assert alerts.alerts is not None
    assert alerts.alert_features == []


def test_ecalerts_init_french():
    alerts = ECAlerts(coordinates=(50, -100), language="french")
    assert alerts.language == "french"
    assert alerts.alerts["warnings"]["label"] == "Alertes"


@pytest.mark.asyncio
async def test_ecalerts_update():
    with open("tests/fixtures/alerts_wfs.json") as f:
        fixture_data = json.load(f)

    resp = AsyncMock()
    resp.json.return_value = fixture_data

    with patch("aiohttp.ClientSession.get", AsyncMock(return_value=resp)):
        ea = ECAlerts(coordinates=(45.33, -75.58))
        await ea.update()

    assert len(ea.alert_features) == 2
    assert ea.alert_features[0]["alert_name_en"] == "Weather Advisory"
    assert ea.alert_features[1]["alert_name_en"] == "Winter Storm Warning"

    assert len(ea.alerts["advisories"]["value"]) == 1
    advisory = ea.alerts["advisories"]["value"][0]
    assert advisory["title"] == "Weather Advisory"
    assert advisory["date"] == "2025-02-05T23:07:00Z"
    assert advisory["expiryTime"] == "2025-02-06T15:07:00Z"
    assert advisory["alertColourLevel"] == "Yellow"
    assert advisory["text"] == "A weather advisory is in effect for the Ottawa region."
    assert advisory["area"] == "Ottawa"
    assert advisory["status"] == "active"
    assert advisory["confidence"] == "Likely"
    assert advisory["impact"] == "Moderate"
    assert advisory["alert_code"] == "AD"

    assert len(ea.alerts["warnings"]["value"]) == 1
    warning = ea.alerts["warnings"]["value"][0]
    assert warning["title"] == "Winter Storm Warning"
    assert warning["alertColourLevel"] == "Red"
    assert warning["alert_code"] == "WS"

    assert ea.alerts["watches"]["value"] == []
    assert ea.alerts["statements"]["value"] == []
    assert ea.alerts["endings"]["value"] == []


@pytest.mark.asyncio
async def test_alert_carries_hazard_start_and_end_times():
    """An alert's bulletin times (`date`, `expiryTime`) aren't when the
    hazard happens: this special weather statement for Calgary was issued
    on the 16th and expires early on the 17th, but the snow it warns of
    starts on the afternoon of the 17th and ends late on the 18th. The WFS
    gives those as `validity_datetime` and `event_end_datetime`; without
    them in the alert dict, Home Assistant's `get_alerts` action, which
    passes the dict through, can't show when the hazard runs (issue #184).
    The fixture is a live capture trimmed to two whole features, one of
    them outside the point, which is filtered out."""
    with open("tests/fixtures/alerts_wfs_calgary.json") as f:
        fixture_data = json.load(f)

    resp = AsyncMock()
    resp.json.return_value = fixture_data

    with patch("aiohttp.ClientSession.get", AsyncMock(return_value=resp)):
        ea = ECAlerts(coordinates=(51.05, -114.07))
        await ea.update()

    assert len(ea.alert_features) == 1
    statements = ea.alerts["statements"]["value"]
    assert len(statements) == 1
    statement = statements[0]
    assert statement["title"] == "Special Weather Statement"
    assert statement["area"] == "City of Calgary"
    assert statement["date"] == "2026-11-16T21:30:28.233Z"
    assert statement["expiryTime"] == "2026-11-17T13:30:28.233Z"
    assert statement["validity_time"] == "2026-11-17T18:00:00.000Z"
    assert statement["event_end_time"] == "2026-11-19T06:00:00.000Z"


@pytest.mark.asyncio
async def test_ecalerts_endings():
    """Test that alerts with status_en='ended' are categorized as endings."""
    fixture = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "alert_name_en": "Weather Advisory",
                    "alert_name_fr": "Avis météorologique",
                    "alert_type": "advisory",
                    "publication_datetime": "2025-02-05T23:07:00Z",
                    "expiration_datetime": "2025-02-06T15:07:00Z",
                    "risk_colour_en": "Yellow",
                    "risk_colour_fr": "Jaune",
                    "status_en": "ended",
                    "status_fr": "terminé",
                    "alert_text_en": "Advisory has ended.",
                    "alert_text_fr": "L'avis est terminé.",
                    "feature_name_en": "Ottawa",
                    "feature_name_fr": "Ottawa",
                    "confidence_en": None,
                    "confidence_fr": None,
                    "impact_en": None,
                    "impact_fr": None,
                    "alert_code": "AD",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-76.0, 45.0],
                            [-75.0, 45.0],
                            [-75.0, 46.0],
                            [-76.0, 46.0],
                            [-76.0, 45.0],
                        ]
                    ],
                },
            }
        ],
    }

    resp = AsyncMock()
    resp.json.return_value = fixture

    with patch("aiohttp.ClientSession.get", AsyncMock(return_value=resp)):
        ea = ECAlerts(coordinates=(45.33, -75.58))
        await ea.update()

    assert len(ea.alerts["endings"]["value"]) == 1
    assert ea.alerts["advisories"]["value"] == []
    ending = ea.alerts["endings"]["value"][0]
    assert ending["title"] == "Weather Advisory"
    assert ending["status"] == "ended"


@pytest.mark.asyncio
async def test_ecalerts_unknown_alert_type_skipped():
    """Test that features with unrecognised alert_type are skipped."""
    fixture = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "alert_name_en": "Unknown Alert",
                    "alert_name_fr": "Alerte inconnue",
                    "alert_type": "unknown_type",
                    "publication_datetime": "2025-02-05T23:07:00Z",
                    "expiration_datetime": "2025-02-06T15:07:00Z",
                    "risk_colour_en": None,
                    "risk_colour_fr": None,
                    "status_en": "active",
                    "status_fr": "actif",
                    "alert_text_en": None,
                    "alert_text_fr": None,
                    "feature_name_en": None,
                    "feature_name_fr": None,
                    "confidence_en": None,
                    "confidence_fr": None,
                    "impact_en": None,
                    "impact_fr": None,
                    "alert_code": "UK",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-76.0, 45.0],
                            [-75.0, 45.0],
                            [-75.0, 46.0],
                            [-76.0, 46.0],
                            [-76.0, 45.0],
                        ]
                    ],
                },
            }
        ],
    }

    resp = AsyncMock()
    resp.json.return_value = fixture

    with patch("aiohttp.ClientSession.get", AsyncMock(return_value=resp)):
        ea = ECAlerts(coordinates=(45.33, -75.58))
        await ea.update()

    # Feature is still in alert_features (raw), but not in any alerts category
    assert len(ea.alert_features) == 1
    total_alerts = sum(len(cat["value"]) for cat in ea.alerts.values())
    assert total_alerts == 0


@pytest.mark.asyncio
async def test_ecalerts_invalid_response_raises():
    """Test that a non-FeatureCollection response raises ValueError."""
    resp = AsyncMock()
    resp.json.return_value = {"error": "not a feature collection"}

    with patch("aiohttp.ClientSession.get", AsyncMock(return_value=resp)):
        ea = ECAlerts(coordinates=(45.33, -75.58))
        with pytest.raises(ValueError):
            await ea.update()


@pytest.mark.asyncio
async def test_ecalerts_french_language():
    """Test that french language returns french labels and field values."""
    with open("tests/fixtures/alerts_wfs.json") as f:
        fixture_data = json.load(f)

    resp = AsyncMock()
    resp.json.return_value = fixture_data

    with patch("aiohttp.ClientSession.get", AsyncMock(return_value=resp)):
        ea = ECAlerts(coordinates=(45.33, -75.58), language="french")
        await ea.update()

    assert ea.alerts["warnings"]["label"] == "Alertes"
    assert len(ea.alerts["warnings"]["value"]) == 1
    warning = ea.alerts["warnings"]["value"][0]
    assert warning["title"] == "Avertissement De Tempête Hivernale"
    assert warning["alertColourLevel"] == "Rouge"
    assert warning["area"] == "Région d'Ottawa"
