"""Provider catalog and response shaping for the wearables routes."""

from typing import Literal
from urllib.parse import parse_qs, urlparse

CLOUD_WEARABLE_PROVIDERS = frozenset(
    {
        "garmin",
        "oura",
        "whoop",
        "polar",
        "suunto",
        "withings",
        "google_health",
        "ultrahuman",
        "strava",
    }
)

# Providers that can supply both numbers of today's overlay.
BALANCE_PROVIDERS = frozenset(
    {"garmin", "oura", "polar", "suunto", "withings", "google_health"}
)

# Steps exist. Active calories do not, so burn stays empty for these. WHOOP
# reports total energy only, with resting burn included.
STEPS_PROVIDERS = frozenset({"ultrahuman", "whoop"})

# Per-activity calories. Listed for the person to add, never summed into the day.
WORKOUT_PROVIDERS = frozenset({"strava"})

ReturnTo = Literal["link", "session"]


def signable_providers(configured: str) -> frozenset[str]:
    """Cloud providers named in OPEN_WEARABLES_PROVIDERS.

    Open Wearables enables every provider by default and hands out a sign-in
    link with an empty client id when no app is registered, so the backend
    keeps its own list of providers that can actually sign in.
    """
    names = {part.strip().lower() for part in configured.split(",")}
    return frozenset(names & CLOUD_WEARABLE_PROVIDERS)


def redirect_uri(provider: str, return_to: ReturnTo) -> str:
    """Where Open Wearables sends the browser after the provider sign-in.

    ``session`` is for the app's in-app sign-in sheet; only its callback
    activity claims the wearables-auth host on Android. ``link`` is the
    deep link older app versions open; Android opens nutree links only
    with the open-nutree host, iOS takes any host.
    """
    if return_to == "session":
        return f"nutree://wearables-auth/{provider}"
    return f"nutree://open-nutree/wearables/connected/{provider}"


def has_client_id(authorization_url: str) -> bool:
    """False for Open Wearables' link to a provider without a registered app."""
    query = parse_qs(urlparse(authorization_url).query)
    return any(value.strip() for value in query.get("client_id", []))


def connection_rows(
    connections: list[dict[str, str]],
    sync_states: dict[str, dict[str, str | None]],
) -> list[dict[str, str | None]]:
    """Connections with the newest sync outcome added.

    ``sync_status`` is ``ok``, ``syncing``, ``failing`` or ``unknown`` (no run
    in the last 24 hours, or the sync lookup failed).
    """
    rows: list[dict[str, str | None]] = []
    for connection in connections:
        state = sync_states.get(connection["provider"], {})
        rows.append(
            {
                **connection,
                "sync_status": state.get("sync_status") or "unknown",
                "last_synced_at": state.get("last_synced_at"),
            }
        )
    return rows
