"""The Firebase service account as the Google Play Developer API signer.

It reads the settings the Firebase Admin SDK starts from (``src/api/main.py``):
the FIREBASE_CREDENTIALS file path first, then FIREBASE_SERVICE_ACCOUNT_JSON.
google-auth, which Firebase Admin is built on, signs the token request.
Nothing read here is logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from google.auth import exceptions as google_auth_exceptions
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account

from src.domain.ports.play_subscription_port import (
    PlayBillingConfigurationError,
    PlayBillingUnavailableError,
)

logger = logging.getLogger(__name__)

ANDROID_PUBLISHER_SCOPE = "https://www.googleapis.com/auth/androidpublisher"


class _BoundedRequest(GoogleAuthRequest):
    """google-auth's requests transport with a fixed timeout on every call."""

    def __init__(self, timeout_seconds: float) -> None:
        super().__init__()
        self._timeout_seconds = timeout_seconds

    def __call__(
        self,
        url: Any,
        method: Any = "GET",
        body: Any = None,
        headers: Any = None,
        timeout: Any = None,
        **kwargs: Any,
    ) -> Any:
        return super().__call__(
            url,
            method=method,
            body=body,
            headers=headers,
            timeout=self._timeout_seconds,
            **kwargs,
        )


class ServiceAccountAccessToken:
    """An androidpublisher access token, refreshed off the event loop when stale."""

    def __init__(
        self,
        signer: Any,
        timeout_seconds: float,
        request: Any | None = None,
    ) -> None:
        self._signer = signer  # google.oauth2.service_account.Credentials
        self._timeout_seconds = timeout_seconds
        self._request = request or _BoundedRequest(timeout_seconds)

    async def get(self) -> str:
        if not self._signer.valid:
            await self._refresh()
        token = self._signer.token
        if not isinstance(token, str) or not token:
            raise PlayBillingConfigurationError("Google issued no access token")
        return token

    async def _refresh(self) -> None:
        # google-auth may retry the token endpoint; the wait stays bounded.
        refresh = asyncio.to_thread(self._signer.refresh, self._request)
        try:
            await asyncio.wait_for(refresh, timeout=self._timeout_seconds * 2)
        except TimeoutError as exc:
            raise PlayBillingUnavailableError("Google token request timed out") from exc
        except google_auth_exceptions.RefreshError as exc:
            if getattr(exc, "retryable", False):
                raise PlayBillingUnavailableError(
                    "Google token request failed"
                ) from exc
            raise PlayBillingConfigurationError(
                "Google refused the service account"
            ) from exc
        except google_auth_exceptions.TransportError as exc:
            raise PlayBillingUnavailableError("Google token request failed") from exc


def load_access_token(
    settings: Any, timeout_seconds: float
) -> ServiceAccountAccessToken | None:
    """The token source, or None (logged) without a usable service account."""
    info = _service_account_info(settings)
    if info is None:
        return None
    try:
        signer = service_account.Credentials.from_service_account_info(
            info, scopes=[ANDROID_PUBLISHER_SCOPE]
        )
    except Exception as exc:  # a malformed key fails in several library types
        logger.error(
            "Firebase service account unusable for Google Play: error=%s",
            type(exc).__name__,
        )
        return None
    return ServiceAccountAccessToken(signer, timeout_seconds)


def _service_account_info(settings: Any) -> dict[str, Any] | None:
    path = settings.FIREBASE_CREDENTIALS
    raw: str | None
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                raw = handle.read()
        except OSError as exc:
            logger.error(
                "Firebase service account file unreadable for Google Play: error=%s",
                type(exc).__name__,
            )
            return None
    else:
        raw = settings.FIREBASE_SERVICE_ACCOUNT_JSON
    if not raw:
        logger.error("No Firebase service account set; Play renewal cancel stays off")
        return None
    try:
        info = json.loads(raw)
    except ValueError:
        logger.error("Firebase service account is not JSON; Play renewal cancel off")
        return None
    if not isinstance(info, dict) or info.get("type") != "service_account":
        logger.error("Firebase service account setting is not a service account key")
        return None
    return info
