"""Feature flags for the subscription access migration."""

import os

_TRUE = {"1", "true", "yes", "on"}


def database_subscription_access_enabled() -> bool:
    """SUBSCRIPTION_ACCESS_ENABLED: the database decides access.

    Off is also the mobile kill switch: /v1/me/entitlements answers 503
    ENTITLEMENTS_DISABLED and the app uses its RevenueCat decision.
    """
    return os.getenv("SUBSCRIPTION_ACCESS_ENABLED", "false").lower() in _TRUE


def subscription_enforcement_enabled() -> bool:
    """SUBSCRIPTION_ENFORCEMENT_ENABLED: paid backend routes answer 402.

    Effective only while SUBSCRIPTION_ACCESS_ENABLED is also on, so turning
    the database authority off (the kill switch) also stops enforcement.
    """
    return (
        database_subscription_access_enabled()
        and os.getenv("SUBSCRIPTION_ENFORCEMENT_ENABLED", "false").lower() in _TRUE
    )
