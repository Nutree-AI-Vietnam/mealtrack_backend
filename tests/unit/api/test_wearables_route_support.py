"""Provider allowlist parsing, return links, sign-in link checks and day bounds."""

from datetime import date

import pytest

from src.api.routes.v1.wearables_route_support import (
    connection_rows,
    has_client_id,
    local_day_bounds,
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


@pytest.mark.parametrize(
    ("timezone", "expected"),
    [
        # Open Wearables keys Vietnam's daily totals to 17:00 UTC the day before.
        ("Asia/Ho_Chi_Minh", ("2026-10-23T17:00:00Z", "2026-10-24T16:59:59Z")),
        ("Asia/Saigon", ("2026-10-23T17:00:00Z", "2026-10-24T16:59:59Z")),
        ("America/Los_Angeles", ("2026-10-24T07:00:00Z", "2026-10-25T06:59:59Z")),
        (None, ("2026-10-24T00:00:00Z", "2026-10-24T23:59:59Z")),
        ("Not/AZone", ("2026-10-24T00:00:00Z", "2026-10-24T23:59:59Z")),
    ],
)
def test_local_day_bounds_cover_the_local_calendar_day(timezone, expected):
    assert local_day_bounds(date(2026, 10, 24), timezone) == expected


def test_local_day_bounds_follow_a_daylight_saving_change():
    # New York's clocks go back on 2026-11-01, so that day lasts 25 hours.
    assert local_day_bounds(date(2026, 11, 1), "America/New_York") == (
        "2026-11-01T04:00:00Z",
        "2026-11-02T04:59:59Z",
    )
