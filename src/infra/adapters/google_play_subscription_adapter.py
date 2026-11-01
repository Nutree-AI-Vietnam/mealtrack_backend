"""Google Play Developer API: turn off a subscription's renewal.

RevenueCat names a Play subscription by its order id (``GPA.…``), while the
cancel call takes the purchase token, so ``orders.get`` turns one into the
other first. ``purchases.subscriptionsv2.cancel`` then stops the next payment
and the paid period runs out as usual.

Calls are signed in as the Firebase service account the backend already
has (``google_play_auth``). Play Console must grant that account
"View financial data" and "Manage orders and subscriptions" on the app.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from src.domain.ports.play_subscription_port import (
    PlayBillingConfigurationError,
    PlayBillingNotFoundError,
    PlayBillingRejectedError,
    PlayBillingUnavailableError,
    PlaySubscriptionPort,
)
from src.infra.adapters.google_play_auth import load_access_token
from src.infra.http import get_shared_http_client

logger = logging.getLogger(__name__)

API_ROOT = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications"
# Developer-requested: the person can't restore the plan from Play, which is
# the point, since lifetime replaced it. Access lasts to the period's end.
CANCEL_BODY = {
    "cancellationContext": {"cancellationType": "DEVELOPER_REQUESTED_STOP_PAYMENTS"}
}
_PACKAGE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+")
_MAX_ID_LENGTH = 4096
_ERROR_WORD = re.compile(r"[A-Za-z_]{1,64}")


class AccessTokenSource(Protocol):
    async def get(self) -> str:
        """A bearer token for the androidpublisher scope."""
        ...


def is_package_name(value: str) -> bool:
    return bool(_PACKAGE_NAME.fullmatch(value))


class GooglePlaySubscriptionAdapter(PlaySubscriptionPort):
    """Cancels Play subscription renewals for one app package."""

    def __init__(
        self,
        *,
        package_name: str,
        access_token: AccessTokenSource | None,
        timeout_seconds: float,
        sandbox_enabled: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.package_name = package_name
        self.enabled = access_token is not None and is_package_name(package_name)
        self.sandbox_enabled = sandbox_enabled
        self._access_token = access_token
        self._timeout_seconds = timeout_seconds
        self._client = client

    @classmethod
    def from_settings(
        cls, settings: Any | None = None
    ) -> GooglePlaySubscriptionAdapter:
        """The adapter for this environment; disabled unless fully configured."""
        if settings is None:
            from src.infra.config.settings import get_settings

            settings = get_settings()
        timeout = settings.GOOGLE_PLAY_API_TIMEOUT_SECONDS
        package_name = settings.GOOGLE_PLAY_PACKAGE_NAME.strip()
        access_token = None
        if settings.GOOGLE_PLAY_RENEWAL_CANCEL_ENABLED:
            if is_package_name(package_name):
                access_token = load_access_token(settings, timeout)
            else:
                logger.error("GOOGLE_PLAY_PACKAGE_NAME is not an application id")
        return cls(
            package_name=package_name,
            access_token=access_token,
            timeout_seconds=timeout,
            sandbox_enabled=settings.GOOGLE_PLAY_CANCEL_SANDBOX,
        )

    async def cancel_renewal(self, order_id: str) -> None:
        if not self.enabled or self._access_token is None:
            raise PlayBillingConfigurationError("Play renewal cancel is not configured")
        order_path = f"orders/{_segment(order_id)}"
        token = await self._access_token.get()
        order = await self._send("orders.get", "GET", order_path, token)
        purchase_token = order.get("purchaseToken")
        if not isinstance(purchase_token, str) or not purchase_token.strip():
            raise PlayBillingRejectedError("Play order carries no purchase token")
        await self._send(
            "subscriptionsv2.cancel",
            "POST",
            f"purchases/subscriptionsv2/tokens/{_segment(purchase_token)}:cancel",
            token,
            json=CANCEL_BODY,
        )

    async def _send(
        self,
        operation: str,
        method: str,
        path: str,
        token: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._client or get_shared_http_client()
        try:
            response = await client.request(
                method,
                f"{API_ROOT}/{self.package_name}/{path}",
                json=json,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise PlayBillingUnavailableError(f"Play {operation} timed out") from exc
        except httpx.HTTPError as exc:
            raise PlayBillingUnavailableError(f"Play {operation} failed") from exc
        _raise_for_status(response, operation)
        if not response.content:
            return {}
        try:
            body = response.json()
        except ValueError as exc:
            raise PlayBillingRejectedError(f"Play {operation} sent no JSON") from exc
        if not isinstance(body, dict):
            raise PlayBillingRejectedError(f"Play {operation} sent no JSON object")
        return body


def _segment(value: str) -> str:
    """One URL path segment from a store id; ids never carry whitespace."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_ID_LENGTH
        or any(char.isspace() for char in value)
    ):
        raise PlayBillingRejectedError("Play id is empty or malformed")
    return quote(value, safe="")


def _raise_for_status(response: httpx.Response, operation: str) -> None:
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
