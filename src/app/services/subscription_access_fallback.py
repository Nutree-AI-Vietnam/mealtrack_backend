"""Legacy fallback when stored subscription facts cannot decide access.

With SUBSCRIPTION_ACCESS_ENABLED on, the access policy raises
SubscriptionAccessUnavailable while a user still holds an unverified row and
nothing else grants access. Sign-in, profile and paid-route checks must not
fail on that: they fall back to the decision the app made before the flag
existed, and this records that they did so the remaining unreconciled users
stay visible.
"""

import logging

from src.observability import increment_metric

logger = logging.getLogger(__name__)


def record_legacy_access_fallback(*, source: str, user_id: object) -> None:
    """Log and count one fallback from the access policy to the legacy check."""
    logger.warning(
        "Subscription access is unresolved in the database; using the legacy "
        "decision: source=%s user_id=%s",
        source,
        user_id,
    )
    increment_metric(
        "subscription_access.legacy_fallback", attributes={"source": source}
    )
