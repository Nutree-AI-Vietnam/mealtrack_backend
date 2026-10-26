"""
Port for subscription services following clean architecture.
Enables subscription checking without depending on specific implementations.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

# Stable, low-cardinality reasons for an unverifiable provider state. They are
# safe to use as log fields, metric attributes and Sentry tags.
SNAPSHOT_REASON_API_KEY_MISSING = "api_key_missing"
SNAPSHOT_REASON_TRANSPORT = "transport"
SNAPSHOT_REASON_HTTP_429 = "http_429"
SNAPSHOT_REASON_HTTP_5XX = "http_5xx"
SNAPSHOT_REASON_HTTP_4XX = "http_4xx"
SNAPSHOT_REASON_MALFORMED = "malformed"
SNAPSHOT_REASON_UNSUPPORTED_STORE = "unsupported_store"
SNAPSHOT_REASON_OWNER_CHANGED = "owner_changed"
SNAPSHOT_REASON_CONFLICTING_FACTS = "conflicting_facts"
SNAPSHOT_REASON_ABSENT_WITH_HISTORY = "absent_with_history"
SNAPSHOT_REASON_IDENTITY_MISMATCH = "identity_mismatch"
SNAPSHOT_REASON_UNEXPECTED = "unexpected"
SNAPSHOT_REASON_UNKNOWN = "unknown"

# Reasons that point at a contract or configuration bug rather than a
# transient provider problem; they need engineering attention immediately.
SNAPSHOT_ERROR_REASONS = frozenset(
    {
        SNAPSHOT_REASON_API_KEY_MISSING,
        SNAPSHOT_REASON_HTTP_4XX,
        SNAPSHOT_REASON_MALFORMED,
        SNAPSHOT_REASON_UNEXPECTED,
        SNAPSHOT_REASON_UNSUPPORTED_STORE,
    }
)

# Provider outages and rate limits. They come in bursts (one per webhook
# during an incident), so they are counted and logged, not sent to Sentry;
# the snapshot_unavailable counter carries the alert.
SNAPSHOT_TRANSIENT_REASONS = frozenset(
    {
        SNAPSHOT_REASON_TRANSPORT,
        SNAPSHOT_REASON_HTTP_429,
        SNAPSHOT_REASON_HTTP_5XX,
    }
)


class RevenueCatSnapshotUnavailable(RuntimeError):
    """Provider state could not be safely verified."""

    def __init__(
        self,
        message: str,
        retry_after_seconds: float | None = None,
        *,
        reason: str = SNAPSHOT_REASON_UNKNOWN,
    ):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.reason = reason


@dataclass(frozen=True)
class RevenueCatSnapshot:
    """One verified read of a RevenueCat customer.

    ``absent`` means RevenueCat holds no purchases for ``app_user_id``: either
    a 404, or the empty customer that v1 GET /subscribers creates on first
    read ("get or create"). ``request_date`` is RevenueCat's own clock at the
    time of the read and orders snapshots against each other and against
    webhook ``event_timestamp_ms`` values.
    """

    subscriber: dict | None
    request_date: datetime | None
    absent: bool = False
    app_user_id: str | None = None


class EntitlementCheck(StrEnum):
    """Answer of the subscription provider for one user.

    ``UNAVAILABLE`` means the provider could not answer; callers must not
    treat it as "not subscribed".
    """

    ENTITLED = "entitled"
    NOT_ENTITLED = "not_entitled"
    UNAVAILABLE = "unavailable"


class SubscriptionServicePort(ABC):
    """Port for subscription operations."""

    @abstractmethod
    async def get_verified_snapshot(self, app_user_id: str) -> RevenueCatSnapshot:
        """Return a verified snapshot or raise on provider/malformed-state errors."""
        pass

    @abstractmethod
    async def get_subscriber_info(self, app_user_id: str) -> dict | None:
        """
        Get subscriber info from subscription provider.

        Args:
            app_user_id: Your user ID

        Returns:
            Subscriber data including entitlements and subscriptions
        """
        pass

    @abstractmethod
    async def has_active_subscription(self, app_user_id: str) -> bool:
        """
        Check if user has active subscription.

        Args:
            app_user_id: Your user ID

        Returns:
            True if user has active subscription, False otherwise
        """
        pass

    @abstractmethod
    async def check_entitlement(
        self, app_user_id: str, *, timeout_seconds: float = 3.0
    ) -> EntitlementCheck:
        """
        Check the standard entitlement, keeping provider failures apart.

        Args:
            app_user_id: Your user ID
            timeout_seconds: Upper bound for the provider call

        Returns:
            ENTITLED or NOT_ENTITLED, or UNAVAILABLE when the provider
            errored or timed out
        """
        pass

    @abstractmethod
    async def get_subscription_info(self, app_user_id: str) -> dict | None:
        """
        Get active subscription details.

        Args:
            app_user_id: Your user ID

        Returns:
            Subscription details (product_id, expires_date, store, is_active) if active
        """
        pass
