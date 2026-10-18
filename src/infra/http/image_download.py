"""Download a scan photo without using the shared 30s connection pool.

The shared client sets ``keepalive_expiry`` to 30s. httpx's own default is
5s. The scan GET also waited 30s for a silent read, and PYTHON-JW timed out
in httpcore's header read after that budget. That wait fits either a
half-open pooled socket or an origin that sent no bytes; the event does not
show which. This client expires idle sockets after 5s and stops a silent
read after 15s. One retry is only a backstop for a timeout, transport error,
or 5xx. It is not treated as a fresh connection. Client 4xx responses are
not retried.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from src.api.exceptions import (
    AuthenticationException,
    AuthorizationException,
    BusinessLogicException,
    ConflictException,
    ExternalServiceException,
    MealTrackException,
    ResourceNotFoundException,
    ValidationException,
)

logger = logging.getLogger(__name__)

# httpx.Limits defaults keepalive_expiry to 5s. The shared client raises that
# to 30s; this download keeps the 5s default so idle sockets are not reused
# for six times as long.
SCAN_IMAGE_KEEPALIVE_EXPIRY_SECONDS = 5.0
SCAN_IMAGE_DOWNLOAD_TIMEOUT = httpx.Timeout(
    connect=5.0,
    read=15.0,
    write=10.0,
    pool=5.0,
)
SCAN_IMAGE_DOWNLOAD_RETRY_BACKOFF_SECONDS = 0.2

_scan_image_client: httpx.AsyncClient | None = None

_CLIENT_STATUS_ERRORS: dict[int, type[MealTrackException]] = {
    400: ValidationException,
    401: AuthenticationException,
    403: AuthorizationException,
    404: ResourceNotFoundException,
    409: ConflictException,
    422: BusinessLogicException,
}


def get_scan_image_http_client() -> httpx.AsyncClient:
    """Return the pooled client used only for scan photo downloads."""
    global _scan_image_client
    if _scan_image_client is None or _scan_image_client.is_closed:
        _scan_image_client = httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=100,
                keepalive_expiry=SCAN_IMAGE_KEEPALIVE_EXPIRY_SECONDS,
            ),
            timeout=SCAN_IMAGE_DOWNLOAD_TIMEOUT,
        )
    return _scan_image_client


async def close_scan_image_http_client() -> None:
    """Close the scan photo client, if it was opened."""
    global _scan_image_client
    if _scan_image_client is not None and not _scan_image_client.is_closed:
        await _scan_image_client.aclose()
    _scan_image_client = None


def _client_error(status_code: int) -> MealTrackException:
    """Map a photo-host 4xx onto the status ``handle_exception`` already uses."""
    message = "The meal photo could not be downloaded."
    if status_code == 404:
        message = "The meal photo could not be found."
    elif status_code == 403:
        message = "The meal photo could not be accessed."
    error_type = _CLIENT_STATUS_ERRORS.get(status_code)
    if error_type is not None:
        return error_type(message=message, error_code=_client_error_code(status_code))
    return MealTrackException(
        message=message,
        error_code="IMAGE_DOWNLOAD_REJECTED",
        status_code=status_code,
    )


def _client_error_code(status_code: int) -> str:
    if status_code == 404:
        return "IMAGE_NOT_FOUND"
    if status_code == 403:
        return "IMAGE_DOWNLOAD_FORBIDDEN"
    return "IMAGE_DOWNLOAD_REJECTED"


async def download_scan_image_bytes(
    image_url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> bytes:
    """GET image bytes. Retry once on timeout, transport failure, or 5xx."""
    http = client if client is not None else get_scan_image_http_client()
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = await http.get(
                image_url,
                timeout=SCAN_IMAGE_DOWNLOAD_TIMEOUT,
            )
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise _client_error(exc.response.status_code) from exc
            last_error = exc
        except httpx.TransportError as exc:
            last_error = exc
        if attempt == 0:
            logger.warning(
                "Meal photo download failed (%s); retrying once",
                type(last_error).__name__,
            )
            await asyncio.sleep(SCAN_IMAGE_DOWNLOAD_RETRY_BACKOFF_SECONDS)
    assert last_error is not None
    raise ExternalServiceException(
        message="The meal photo is temporarily unavailable. Please try again.",
        error_code="IMAGE_DOWNLOAD_UNAVAILABLE",
    ) from last_error
