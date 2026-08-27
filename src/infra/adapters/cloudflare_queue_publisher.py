"""HTTP publisher for Cloudflare Queue messages."""

from __future__ import annotations

from typing import Any

import httpx

from src.infra.http import get_shared_http_client


class CloudflareQueueError(Exception):
    """Base error for Queue publication failures."""


class CloudflareQueueConfigurationError(CloudflareQueueError):
    """Raised when Queue credentials or endpoint configuration is invalid."""


class CloudflareQueueTransientError(CloudflareQueueError):
    """Raised for Queue failures that should be retried."""


class CloudflareQueuePermanentError(CloudflareQueueError):
    """Raised for a rejected or malformed Queue request."""


class CloudflareQueuePublisher:
    """Publish one JSON event to the Cloudflare Queue HTTP API."""

    def __init__(
        self,
        *,
        account_id: str,
        queue_name: str,
        api_token: str,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._account_id = account_id
        self._queue_name = queue_name
        self._api_token = api_token
        self._timeout_seconds = timeout_seconds
        self._client = client

    @classmethod
    def from_settings(
        cls,
        *,
        queue_name: str | None = None,
    ) -> CloudflareQueuePublisher:
        from src.infra.config.settings import get_settings

        settings = get_settings()
        return cls(
            account_id=settings.CLOUDFLARE_QUEUE_ACCOUNT_ID,
            queue_name=(
                settings.CLOUDFLARE_QUEUE_NAME if queue_name is None else queue_name
            ),
            api_token=settings.CLOUDFLARE_QUEUE_API_TOKEN,
            timeout_seconds=settings.CLOUDFLARE_QUEUE_TIMEOUT_SECONDS,
        )

    @property
    def queue_name(self) -> str:
        """Configured destination name for observability and routing checks."""
        return self._queue_name

    @property
    def endpoint(self) -> str:
        return (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self._account_id}/queues/{self._queue_name}/messages"
        )

    def _validate_configuration(self) -> None:
        if not self._account_id or not self._queue_name or not self._api_token:
            raise CloudflareQueueConfigurationError(
                "Cloudflare Queue account, name, and token are required"
            )

    async def publish(self, payload: dict[str, Any]) -> None:
        """Publish payload and raise a classified error when it is not accepted."""
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
                f"Queue returned retryable status {response.status_code}"
            )
        if response.status_code in {401, 403}:
            raise CloudflareQueueConfigurationError(
                f"Queue credentials rejected with status {response.status_code}"
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise CloudflareQueuePermanentError(
                f"Queue rejected request with status {response.status_code}"
            )

        try:
            result = response.json()
        except ValueError as exc:
            raise CloudflareQueuePermanentError("Queue returned invalid JSON") from exc
        if not isinstance(result, dict) or result.get("success") is not True:
            raise CloudflareQueuePermanentError("Queue did not confirm acceptance")
