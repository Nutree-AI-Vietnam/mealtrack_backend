"""Download a scan photo through the shared HTTP client.

The client keeps HTTP/1.1 connections alive. When the photo host has already
closed one, httpcore blocks in `_receive_response_headers` until ReadTimeout
and only then drops the socket. One retry opens a new connection. A second
timeout or 5xx is `ImageDownloadUnavailable`; the composition root turns that
into the upstream-unavailable 503. Client 4xx responses are not retried.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

# Read stays at the previous 30s silence budget. A bare float would also
# stretch connect and pool to 30s; those stay short.
SCAN_IMAGE_DOWNLOAD_TIMEOUT = httpx.Timeout(
    connect=5.0,
    read=30.0,
    write=10.0,
    pool=5.0,
)
SCAN_IMAGE_DOWNLOAD_RETRY_BACKOFF_SECONDS = 0.2


class ImageDownloadUnavailable(Exception):
    """Photo host failed twice on a timeout or transient upstream error."""


async def download_scan_image_bytes(image_url: str) -> bytes:
    """GET image bytes, retrying once after a timeout, transport error, or 5xx."""
    from src.infra.http import get_shared_http_client

    client = get_shared_http_client()
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = await client.get(
                image_url,
                timeout=SCAN_IMAGE_DOWNLOAD_TIMEOUT,
            )
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise
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
    raise ImageDownloadUnavailable(type(last_error).__name__) from last_error
