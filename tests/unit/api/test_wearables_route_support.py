"""Provider allowlist parsing, return links and sign-in link checks."""

import pytest

from src.api.routes.v1.wearables_route_support import (
    connection_rows,
    has_client_id,
    redirect_uri,
    signable_providers,
)


def test_signable_providers_keeps_known_cloud_providers_only():
    assert signable_providers("") == frozenset()
    assert signable_providers(" google_health , GARMIN,apple,fitbit,,") == frozenset(
        {"google_health", "garmin"}
    )


def test_redirect_uri_per_return_target():
    assert redirect_uri("oura", "link") == (
        "nutree://open-nutree/wearables/connected/oura"
    )
    assert redirect_uri("oura", "session") == "nutree://wearables-auth/oura"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://accounts.google.com/o/oauth2/v2/auth?client_id=abc&x=1", True),
        ("https://connect.garmin.com/oauth2Confirm?client_id=&state=s", False),
        ("https://cloud.ouraring.com/oauth/authorize?state=s", False),
        ("https://www.strava.com/oauth/authorize?client_id=%20%20", False),
        ("not a url", False),
    ],
)
def test_has_client_id(url, expected):
    assert has_client_id(url) is expected


def test_connection_rows_default_to_unknown_sync():
    rows = connection_rows(
        [{"provider": "oura", "status": "expired"}],
        {"garmin": {"sync_status": "ok", "last_synced_at": "2026-10-24T08:00:00Z"}},
    )
    assert rows == [
        {
            "provider": "oura",
            "status": "expired",
            "sync_status": "unknown",
            "last_synced_at": None,
        }
    ]
