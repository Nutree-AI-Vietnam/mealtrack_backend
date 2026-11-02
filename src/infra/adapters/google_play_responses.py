"""Reading Google Play Developer API answers.

Status codes become typed errors, and a ``SubscriptionPurchaseV2`` says
whether the plan still auto-renews.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from src.domain.ports.play_subscription_port import (
    PlayBillingConfigurationError,
    PlayBillingNotFoundError,
    PlayBillingRejectedError,
    PlayBillingUnavailableError,
)

# Play states in which the plan will charge again unless renewal is turned
# off. A CANCELED plan already has every item's auto-renew off.
RENEWING_STATES = frozenset(
    {
        "SUBSCRIPTION_STATE_ACTIVE",
        "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
        "SUBSCRIPTION_STATE_ON_HOLD",
        "SUBSCRIPTION_STATE_PAUSED",
    }
)
_ERROR_WORD = re.compile(r"[A-Za-z_]{1,64}")


def is_retryable_status(status: int) -> bool:
    """5xx only: a 4xx, 408 and 429 included, is not retried."""
    return status >= 500


def raise_for_status(response: httpx.Response, operation: str) -> None:
    status = response.status_code
    if 200 <= status < 300:
        return
    message = f"Play {operation} returned {status}{_error_detail(response)}"
    if status in (401, 403):
        raise PlayBillingConfigurationError(message, status_code=status)
    if status in (404, 410):
        raise PlayBillingNotFoundError(message, status_code=status)
    if status in (408, 429) or status >= 500:
        raise PlayBillingUnavailableError(message, status_code=status)
    raise PlayBillingRejectedError(message, status_code=status)


def still_renews(subscription: dict[str, Any], product_id: str) -> bool:
    """Whether the plan's state and its own line item still auto-renew.

    The subscription must hold ``product_id``: an order that names another
    product is a mix-up, not a plan to leave alone.
    """
    items = subscription.get("lineItems")
    item = next(
        (
            entry
            for entry in (items if isinstance(items, list) else [])
            if isinstance(entry, dict) and entry.get("productId") == product_id
        ),
        None,
    )
    if item is None:
        raise PlayBillingRejectedError("Play subscription does not hold the product")
    plan = item.get("autoRenewingPlan")
    # proto3 JSON leaves out a false boolean, so a missing flag means "off".
    auto_renew = isinstance(plan, dict) and plan.get("autoRenewEnabled") is True
    return subscription.get("subscriptionState") in RENEWING_STATES and auto_renew


def _error_detail(response: httpx.Response) -> str:
    """Google's status and reason words only; messages may echo ids."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return ""
    if not isinstance(error, dict):
        return ""
    errors = error.get("errors")
    first = errors[0] if isinstance(errors, list) and errors else {}
    words = [
        error.get("status"),
        first.get("reason") if isinstance(first, dict) else None,
    ]
    kept = [w for w in words if isinstance(w, str) and _ERROR_WORD.fullmatch(w)]
    return f" ({', '.join(kept)})" if kept else ""
