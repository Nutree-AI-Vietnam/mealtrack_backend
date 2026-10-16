"""RevenueCat REST v1 implementation of ``BillingProviderPort`` (D10).

Unlike the legacy ``RevenueCatAdapter`` (kept for its callers until P6), every
failure is a typed error, so a failed fetch can never read as "not subscribed".
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from src.domain.exceptions.billing_exceptions import (
    InvalidProviderPayload,
    ProviderNotFound,
    ProviderRateLimited,
    ProviderUnavailable,
)
from src.domain.model.billing.constants import PROVIDER_REVENUECAT
from src.domain.model.billing.snapshot import CustomerSnapshot
from src.domain.ports.billing_provider_port import BillingProviderPort
from src.domain.services.billing.revenuecat_normalizer import (
    normalize_revenuecat_subscriber,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.config.settings import settings

logger = logging.getLogger(__name__)

# RevenueCat's "too many requests" error code, sometimes sent without a 429.
RATE_LIMIT_ERROR_CODE = 7638


class RevenueCatBillingProvider(BillingProviderPort):
    provider = PROVIDER_REVENUECAT

    BASE_URL = "https://api.revenuecat.com/v1"
    TIMEOUT_SECONDS = 10.0

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = BASE_URL,
        timeout_seconds: float = TIMEOUT_SECONDS,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._api_key = api_key or settings.REVENUECAT_SECRET_API_KEY or ""
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._clock = clock

    async def fetch_customer(self, customer_id: str) -> CustomerSnapshot:
        if not self._api_key:
            raise ProviderUnavailable(
                "RevenueCat secret API key is not configured", provider=self.provider
            )
        # Snapshot time = fetch start, so a slower, older read never wins.
        fetched_at = self._clock()
        response = await self._get(
            f"{self._base_url}/subscribers/{quote(customer_id, safe='')}"
        )
        status = response.status_code
        if status == 404:
            raise ProviderNotFound(
                "RevenueCat customer not found", provider=self.provider
            )
        body = _json_or_none(response)
        if status == 429 or (
            isinstance(body, Mapping) and body.get("code") == RATE_LIMIT_ERROR_CODE
        ):
            raise ProviderRateLimited(
                f"RevenueCat rate limited (HTTP {status})",
                provider=self.provider,
                retry_after_seconds=_retry_after_seconds(response, body),
            )
        if not 200 <= status < 300:
            logger.warning(
                "RevenueCat subscriber fetch failed",
                extra={"status_code": status, "provider": self.provider},
            )
            raise ProviderUnavailable(
                f"RevenueCat returned HTTP {status}", provider=self.provider
            )
        if not isinstance(body, Mapping):
            raise InvalidProviderPayload(
                "RevenueCat returned a non-object body", provider=self.provider
            )
        return normalize_revenuecat_subscriber(
            body, fetched_at=fetched_at, requested_customer_id=customer_id
        )

    async def _get(self, url: str) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        try:
            if self._client is not None:
                return await self._client.get(
                    url, headers=headers, timeout=self._timeout
                )
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                return await client.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderUnavailable(
                "RevenueCat request timed out", provider=self.provider
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"RevenueCat transport error: {type(exc).__name__}",
                provider=self.provider,
            ) from exc


def _json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _retry_after_seconds(response: httpx.Response, body: Any) -> float | None:
    header = response.headers.get("Retry-After")
    if header:
        try:
            return max(float(header), 0.0)
        except ValueError:
            pass
    if isinstance(body, Mapping):
        backoff_ms = body.get("backoff_ms")
        if isinstance(backoff_ms, int | float) and backoff_ms >= 0:
            return backoff_ms / 1000.0
    return None
