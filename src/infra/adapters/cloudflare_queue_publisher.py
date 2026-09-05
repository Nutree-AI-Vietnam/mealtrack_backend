"""HTTP publisher for Cloudflare Queue messages."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import httpx

from src.infra.http import get_shared_http_client

logger = logging.getLogger(__name__)


class CloudflareQueueError(Exception):
    """Base error for Queue publication failures."""


class CloudflareQueueConfigurationError(CloudflareQueueError):
    """Raised when Queue credentials or endpoint configuration is invalid."""


class CloudflareQueueTransientError(CloudflareQueueError):
    """Raised for Queue failures that should be retried."""


class CloudflareQueuePermanentError(CloudflareQueueError):
    """Raised for a rejected or malformed Queue request."""


def _cloudflare_error_detail(response: httpx.Response) -> str:
    """Return bounded provider error details without exposing request payloads."""
    try:
        result = response.json()
    except ValueError:
        return ""

    if not isinstance(result, Mapping) or not isinstance(result.get("errors"), list):
        return ""

    details: list[str] = []
    for error in result["errors"][:3]:
        if not isinstance(error, Mapping):
            continue
        code = error.get("code")
        message = error.get("message")
        if isinstance(code, (int, str)) and isinstance(message, str):
            details.append(f"{code}: {message[:160]}")
    return "; ".join(details)


def _status_message(response: httpx.Response, prefix: str) -> str:
    detail = _cloudflare_error_detail(response)
    return f"{prefix}: {detail}" if detail else prefix


class CloudflareQueuePublisher:
    """Publish one JSON event to the Cloudflare Queue HTTP API."""

    def __init__(
        self,
        *,
        account_id: str,
        queue_id: str,
        api_token: str,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._account_id = account_id
        self._queue_id = queue_id
        self._api_token = api_token
        self._timeout_seconds = timeout_seconds
        self._client = client

    @classmethod
    def from_settings(
        cls,
        *,
        queue_id: str | None = None,
    ) -> CloudflareQueuePublisher:
        from src.infra.config.settings import get_settings

        settings = get_settings()
        return cls(
            account_id=(
                settings.CLOUDFLARE_QUEUE_ACCOUNT_ID or settings.CLOUDFLARE_ACCOUNT_ID
            ),
            queue_id=(settings.CLOUDFLARE_QUEUE_ID if queue_id is None else queue_id),
            api_token=(
                settings.CLOUDFLARE_QUEUE_API_TOKEN or settings.CLOUDFLARE_API_TOKEN
            ),
            timeout_seconds=settings.CLOUDFLARE_QUEUE_TIMEOUT_SECONDS,
        )

    @property
    def queue_id(self) -> str:
        """Configured Cloudflare Queue resource identifier."""
        return self._queue_id

    @property
    def endpoint(self) -> str:
        return (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self._account_id}/queues/{self._queue_id}/messages"
        )

    def _has_credentials(self) -> bool:
        return bool(self._account_id and self._queue_id and self._api_token)

    def _validate_configuration(self) -> None:
        if not self._has_credentials():
            raise CloudflareQueueConfigurationError(
                "Cloudflare Queue account, ID, and token are required"
            )
        if not (self._queue_id.isascii() and self._queue_id.isalnum()):
            raise CloudflareQueueConfigurationError(
                "Cloudflare Queue ID must contain only alphanumeric characters"
            )

    async def publish(self, payload: dict[str, Any]) -> None:
        """Publish payload and raise a classified error when it is not accepted."""
        if not self._has_credentials():
            logger.warning("cloudflare queue skipped: missing account, ID, or token")
            return
        self._validate_configuration()
        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
        }
        body = {"body": payload}

        try:
            client = self._client or get_shared_http_client()
            response = await client.post(
                self.endpoint,
                json=body,
                headers=headers,
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise CloudflareQueueTransientError("Queue request timed out") from exc
        except httpx.HTTPError as exc:
            raise CloudflareQueueTransientError("Queue request failed") from exc

        if (
            response.status_code == 408
            or response.status_code == 429
            or response.status_code >= 500
        ):
            raise CloudflareQueueTransientError(
                _status_message(
                    response,
                    f"Queue returned retryable status {response.status_code}",
                )
            )
        if response.status_code in {401, 403}:
            raise CloudflareQueueConfigurationError(
                _status_message(
                    response,
                    f"Queue credentials rejected with status {response.status_code}",
                )
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise CloudflareQueuePermanentError(
                _status_message(
                    response,
                    f"Queue rejected request with status {response.status_code}",
                )
            )

        try:
            result = response.json()
        except ValueError as exc:
            raise CloudflareQueuePermanentError("Queue returned invalid JSON") from exc
        if not isinstance(result, dict) or result.get("success") is not True:
            detail = _cloudflare_error_detail(response)
            message = "Queue did not confirm acceptance"
            raise CloudflareQueuePermanentError(
                f"{message}: {detail}" if detail else message
            )
