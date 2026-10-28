"""Port for re-reading a user's provider state into the access rows.

This is the self-heal path for the database access authority: a missed or
failed webhook leaves stale rows until something reads the provider again.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

# The provider customer was read but could not be applied safely; the rows
# were left exactly as they were (see ``reason``).
REFRESH_NOT_APPLIED = "not_applied"


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
    "absent") or REFRESH_NOT_APPLIED with a stable snapshot ``reason``.
    """

    result: str
    reason: str | None = None


class SubscriptionAccessRefreshPort(ABC):
    @abstractmethod
    async def refresh(self, user_id: str) -> SubscriptionAccessRefresh:
        """Read the user's provider customer and apply it to their access rows.

        Commits what it applied. Raises RevenueCatSnapshotUnavailable when
        the customer cannot be read (``retry_after_seconds`` is set when the
        provider asked for a delay), and SubscriptionAccessApplyError when
        applying it failed unexpectedly.
        """
