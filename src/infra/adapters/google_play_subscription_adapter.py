"""Google Play Developer API: turn off a subscription's renewal.

RevenueCat names a Play subscription by its order id (``GPA.…``), while the
subscription calls take the purchase token, so ``orders.get`` turns one into
the other first. ``purchases.subscriptionsv2.get`` then says whether the plan
still auto-renews, and only then does ``purchases.subscriptionsv2.cancel``
stop the next payment. The paid period runs out as usual.

Calls are signed in as the Firebase service account the backend already
has (``google_play_auth``). Play Console must grant that account "View
financial data" and "Manage orders and subscriptions" on this app only.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from src.domain.ports.play_subscription_port import (
    PlayBillingConfigurationError,
    PlayBillingRejectedError,
    PlayBillingUnavailableError,
    PlayCancelOutcome,
    PlaySubscriptionPort,
)
from src.infra.adapters.google_play_auth import load_access_token
from src.infra.adapters.google_play_responses import (
    is_retryable_status,
    raise_for_status,
    still_renews,
)
from src.infra.http import get_shared_http_client

logger = logging.getLogger(__name__)

API_ROOT = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications"
# Developer-requested: the person can't restore the plan from Play, which is
# the point, since lifetime replaced it. Access lasts to the period's end.
CANCEL_BODY = {
    "cancellationContext": {"cancellationType": "DEVELOPER_REQUESTED_STOP_PAYMENTS"}
}
RETRY_DELAY_SECONDS = 0.25
_PACKAGE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+")
_MAX_ID_LENGTH = 4096


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
        switched_on: bool = True,
        sandbox_enabled: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.package_name = package_name
        self.switched_on = switched_on
        self.enabled = (
            switched_on and access_token is not None and is_package_name(package_name)
        )
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
        switched_on = bool(settings.GOOGLE_PLAY_RENEWAL_CANCEL_ENABLED)
        timeout = float(settings.GOOGLE_PLAY_API_TIMEOUT_SECONDS)
        package_name = str(settings.GOOGLE_PLAY_PACKAGE_NAME or "").strip()
        access_token = None
        if switched_on:
            if is_package_name(package_name):
                access_token = load_access_token(
                    settings.FIREBASE_CREDENTIALS,
                    settings.FIREBASE_SERVICE_ACCOUNT_JSON,
                    timeout,
                )
            else:
                logger.error("GOOGLE_PLAY_PACKAGE_NAME is not an application id")
        return cls(
            package_name=package_name,
            access_token=access_token,
            timeout_seconds=timeout,
            switched_on=switched_on,
            sandbox_enabled=bool(settings.GOOGLE_PLAY_CANCEL_SANDBOX),
        )

    async def cancel_renewal(self, order_id: str, product_id: str) -> PlayCancelOutcome:
        if not self.enabled or self._access_token is None:
            raise PlayBillingConfigurationError("Play renewal cancel is not configured")
        order_path = f"orders/{_segment(order_id)}"
        product = product_id.split(":", 1)[0] if isinstance(product_id, str) else ""
        if not product:
            raise PlayBillingRejectedError("No Play product to match the order to")
        token = await self._access_token.get()
        order = await self._send("orders.get", "GET", order_path, token)
        purchase_token = order.get("purchaseToken")
        if not isinstance(purchase_token, str) or not purchase_token.strip():
            raise PlayBillingRejectedError("Play order carries no purchase token")
        path = f"purchases/subscriptionsv2/tokens/{_segment(purchase_token)}"
        subscription = await self._send("subscriptionsv2.get", "GET", path, token)
        if not still_renews(subscription, product):
            return PlayCancelOutcome.NOT_RENEWING
        await self._send(
            "subscriptionsv2.cancel", "POST", f"{path}:cancel", token, json=CANCEL_BODY
        )
        return PlayCancelOutcome.CANCELLED

    async def _send(
        self,
        operation: str,
        method: str,
        path: str,
        token: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One call, tried a second time after a timeout, network failure or 5xx."""
        try:
            return await self._send_once(operation, method, path, token, json)
        except PlayBillingUnavailableError as exc:
            if exc.status_code is not None and not is_retryable_status(exc.status_code):
                raise
        await asyncio.sleep(RETRY_DELAY_SECONDS)
        return await self._send_once(operation, method, path, token, json)

    async def _send_once(
        self,
        operation: str,
        method: str,
        path: str,
        token: str,
        json: dict[str, Any] | None,
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
        raise_for_status(response, operation)
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
