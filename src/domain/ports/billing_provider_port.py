"""Port for reading a customer's purchases from a billing provider (D10).

Replaces ``SubscriptionServicePort`` for the entitlement ledger path. Errors are
typed and never collapse into "not subscribed"; the legacy RevenueCat adapter
keeps its ``None``-on-error contract for its existing callers.
"""

from abc import ABC, abstractmethod

from src.domain.exceptions.billing_exceptions import (
    BillingProviderError,
    InvalidProviderPayload,
    ProviderNotFound,
    ProviderRateLimited,
    ProviderUnavailable,
)
from src.domain.model.billing.snapshot import CustomerSnapshot

__all__ = [
    "BillingProviderError",
    "BillingProviderPort",
    "InvalidProviderPayload",
    "ProviderNotFound",
    "ProviderRateLimited",
    "ProviderUnavailable",
]


class BillingProviderPort(ABC):
    """Fetch one customer's current purchases as a normalized snapshot."""

    provider: str

    @abstractmethod
    async def fetch_customer(self, customer_id: str) -> CustomerSnapshot:
        """Return the provider's current view of ``customer_id``.

        Callers run this outside any DB transaction, after acquiring the shared
        provider budget.

        Raises:
            ProviderUnavailable: timeout, transport error or 5xx.
            ProviderRateLimited: 429 or the provider's rate-limit error code.
            ProviderNotFound: the provider does not know ``customer_id``.
            InvalidProviderPayload: the body cannot be normalized safely.
        """
