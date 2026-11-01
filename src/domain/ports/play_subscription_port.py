"""Port for turning off a Google Play subscription's renewal from the backend.

The paid period is kept: the subscription simply doesn't charge again. The
App Store has no such call, so only Play plans can be stopped this way.
"""

from __future__ import annotations

from typing import Protocol


class PlayBillingError(Exception):
    """A Google Play Developer API call did not do what was asked."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PlayBillingConfigurationError(PlayBillingError):
    """Unusable credentials or settings, or Play refused access (401/403)."""


class PlayBillingNotFoundError(PlayBillingError):
    """Play knows no such order or purchase for the package (404/410)."""


class PlayBillingUnavailableError(PlayBillingError):
    """Play or the network failed for now: a timeout, 408, 429 or 5xx."""


class PlayBillingRejectedError(PlayBillingError):
    """Play rejected the request, or answered with something unusable."""


class PlaySubscriptionPort(Protocol):
    """Cancels the renewal of a Play subscription named by its order id."""

    # The switch is on and the credentials and package name are usable.
    enabled: bool
    # Sandbox (license tester) purchases are acted on too.
    sandbox_enabled: bool

    async def cancel_renewal(self, order_id: str) -> None:
        """Stop the next renewal of the subscription that ``order_id`` paid for.

        Any order of the subscription names it, the first or a renewal.
        Raises a ``PlayBillingError`` subclass when Play doesn't confirm.
        """
        ...
