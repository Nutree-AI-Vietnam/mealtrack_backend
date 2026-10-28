"""Port for re-reading a user's provider state into the access rows.

This is the self-heal path for the database access authority: a missed or
failed webhook leaves stale rows until something reads the provider again.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

# What applying one provider snapshot did to the user's access rows.
SyncResult = Literal["applied", "unchanged", "stale_skipped", "absent"]
# A refresh's result: a sync result, or REFRESH_NOT_APPLIED.
RefreshResult = SyncResult | Literal["not_applied"]
# The status a provider entitlement would be stored with.
ProviderEntitlementStatus = Literal["active", "cancelled", "billing_issue", "expired"]

# The provider customer was read but could not be applied safely; the rows
# were left exactly as they were (see ``reason``).
REFRESH_NOT_APPLIED: Final = "not_applied"
# ``source`` of the signed-in user's own refresh (POST /v1/me/entitlements/refresh).
REFRESH_SOURCE = "refresh"


class SubscriptionAccessApplyError(RuntimeError):
    """A provider customer was read, but applying it failed unexpectedly.

    The cause (a bug or a database failure) is chained as ``__cause__`` and
    was already reported where it happened, with a fixed Sentry fingerprint;
    callers answer the failure without reporting it again. Nothing was
    written: the unit of work rolled back.
    """


@dataclass(frozen=True)
class SubscriptionAccessRefresh:
    """What one refresh did to the user's access rows.

    ``result`` is the sync outcome ("applied", "unchanged", "stale_skipped",
    "absent") or REFRESH_NOT_APPLIED with a stable snapshot ``reason``. The
    counts describe an applied snapshot: rows it revoked, whether it created
    a row, sync-required markers it cleared, and whether the customer had
    entitlements but none named 'standard'.
    """

    result: RefreshResult
    reason: str | None = None
    rows_revoked: int = 0
    row_created: bool = False
    markers_cleared: int = 0
    standard_missing: bool = False


@dataclass(frozen=True)
class ProviderEntitlement:
    """The provider's 'standard' entitlement as one read describes it.

    ``status`` is what applying the read would store: active, cancelled
    (paid through, will not renew), billing_issue, or expired (the only one
    without access).
    """

    has_access: bool
    status: ProviderEntitlementStatus
    product_id: str
    platform: str
    purchased_at: datetime
    expires_at: datetime | None
    grace_period_expires_at: datetime | None
    period_type: str
    is_sandbox: bool
    will_renew: bool


@dataclass(frozen=True)
class ProviderAccessPeek:
    """One read of a provider customer that nothing was written from.

    ``absent``: the provider holds no purchases for the user. ``entitlements``
    names every entitlement the customer has; ``standard`` is None when none
    is named 'standard'.
    """

    read_at: datetime | None
    absent: bool
    entitlements: tuple[str, ...]
    standard: ProviderEntitlement | None

    @property
    def standard_missing(self) -> bool:
        return bool(self.entitlements) and "standard" not in self.entitlements


class SubscriptionAccessRefreshPort(ABC):
    @abstractmethod
    async def refresh(
        self, user_id: str, *, source: str = REFRESH_SOURCE
    ) -> SubscriptionAccessRefresh:
        """Read the user's provider customer and apply it to their access rows.

        Commits what it applied. Raises RevenueCatSnapshotUnavailable when
        the customer cannot be read (``retry_after_seconds`` is set when the
        provider asked for a delay), and SubscriptionAccessApplyError when
        applying it failed unexpectedly. ``source`` labels the logs, metrics
        and Sentry events (the user's own refresh, or a support resync).
        """

    @abstractmethod
    async def peek(self, user_id: str, *, source: str) -> ProviderAccessPeek:
        """Read the user's provider customer without applying or writing it.

        RevenueCat's GET /v1/subscribers is get-or-create, so call it only
        when someone asked for a live read. Raises
        RevenueCatSnapshotUnavailable when the customer cannot be read or
        understood, or the user has no active Firebase identity.
        """
