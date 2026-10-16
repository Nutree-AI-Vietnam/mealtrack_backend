"""Errors raised by billing providers and the entitlement ledger.

Provider errors are typed so callers can keep the last known projection instead
of reading a failed fetch as "not subscribed".
"""


class BillingProviderError(Exception):
    """Base class for a failed billing-provider read. Never reduces access."""

    code = "provider_error"

    def __init__(self, message: str = "", *, provider: str | None = None) -> None:
        super().__init__(message or self.code)
        self.provider = provider


class ProviderUnavailable(BillingProviderError):
    """Timeout, transport error, 5xx or an open circuit breaker."""

    code = "provider_unavailable"


class ProviderRateLimited(BillingProviderError):
    """HTTP 429 or RevenueCat error code 7638."""

    code = "provider_rate_limited"

    def __init__(
        self,
        message: str = "",
        *,
        provider: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message, provider=provider)
        self.retry_after_seconds = retry_after_seconds


class ProviderNotFound(BillingProviderError):
    """The provider does not know this customer id."""

    code = "provider_not_found"


class InvalidProviderPayload(BillingProviderError):
    """The provider answered, but the body cannot be normalized safely."""

    code = "provider_invalid_payload"


class InvalidEntitlementGrant(ValueError):
    """A manual grant request breaks the D5 rules (window, source or reason)."""
