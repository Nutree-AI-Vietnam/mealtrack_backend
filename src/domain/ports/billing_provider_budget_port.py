"""Port for the shared, prioritised billing-provider request budget (NM-680)."""

from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager


class BillingProviderBudgetPort(ABC):
    """One budget for every billing-provider read, shared by all sync lanes.

    Usage::

        async with budget.acquire(SyncLane.WEBHOOK):
            snapshot = await provider.fetch_customer(customer_id)

    The context manager also observes how the call ended, so provider errors
    raised inside it feed the budget's back-off and circuit breaker.
    """

    @abstractmethod
    def acquire(
        self, lane: str, timeout: float | None = None
    ) -> AbstractAsyncContextManager[None]:
        """Wait for a request token, higher-priority lanes first, and hold a
        concurrency slot for the duration of the block.

        Raises:
            ProviderRateLimited: no token within ``timeout`` (or the lane default).
            ProviderUnavailable: the circuit breaker is open.
        """
