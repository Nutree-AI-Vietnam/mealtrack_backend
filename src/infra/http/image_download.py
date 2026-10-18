"""Download a scan photo without using the shared 30s connection pool.

The shared client sets ``keepalive_expiry`` to 30s. httpx's own default is
5s. The scan GET also waited 30s for a silent read, and PYTHON-JW timed out
in httpcore's header read after that budget. That wait fits either a
half-open pooled socket or an origin that sent no bytes; the event does not
show which. This client expires idle sockets after 5s and stops a silent
read after 15s. One retry is only a backstop for a timeout, transport error,
or 5xx. It is not treated as a fresh connection. A photo-host 404
becomes our 404. Every other photo-host 4xx becomes 422
``IMAGE_DOWNLOAD_REJECTED`` and is not retried. Those responses are never
401 or 403, which the app treats as a bad session.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from src.api.exceptions import (
    ExternalServiceException,
    ImageDownloadRejected,
    MealTrackException,
    ResourceNotFoundException,
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
# Two 15s reads plus the backoff, with a little room past the per-attempt caps.
SCAN_IMAGE_DOWNLOAD_DEADLINE_SECONDS = 35.0

_scan_image_client: httpx.AsyncClient | None = None


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
    """Map a photo-host 4xx without echoing auth or rate-limit statuses.

    404 is a missing photo. Any other 4xx, including 401, 403, and 429, is
    one client error. Passing 401/403 through makes the app drop the session.
    """
    if status_code == 404:
        return ResourceNotFoundException(
            message="The meal photo could not be found.",
            error_code="IMAGE_NOT_FOUND",
        )
    return ImageDownloadRejected()


class ScanImageDownloadAdapter:
    """HTTP adapter for :class:`src.app.ports.image_download_port.ImageDownloadPort`.

    Timeouts and status mapping stay in ``download_scan_image_bytes``:
    keepalive 5s, read 15s, 35s overall cap, upstream 404 as
    ``IMAGE_NOT_FOUND``, and any other 4xx as 422 ``IMAGE_DOWNLOAD_REJECTED``.
    """

    async def __call__(self, image_url: str) -> bytes:
        return await download_scan_image_bytes(image_url)


async def download_scan_image_bytes(
    image_url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> bytes:
    """GET image bytes. Retry once on timeout, transport failure, or 5xx."""
    http = client if client is not None else get_scan_image_http_client()
    try:
        async with asyncio.timeout(SCAN_IMAGE_DOWNLOAD_DEADLINE_SECONDS):
            return await _download_with_one_retry(http, image_url)
    except TimeoutError as exc:
        raise ExternalServiceException(
            message="The meal photo is temporarily unavailable. Please try again.",
            error_code="IMAGE_DOWNLOAD_UNAVAILABLE",
        ) from exc


async def _download_with_one_retry(http: httpx.AsyncClient, image_url: str) -> bytes:
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
