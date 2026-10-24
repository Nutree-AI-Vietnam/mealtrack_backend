"""Feature flags for the subscription access migration."""

import os


def database_subscription_access_enabled() -> bool:
    return os.getenv("SUBSCRIPTION_ACCESS_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
